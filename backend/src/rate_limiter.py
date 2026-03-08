"""
Rate Limiter for API Requests (3 RPM, 10,000 TPM)

Implements a balanced strategy with sliding window for managing API rate limits.
Based on the pseudocode provided for VoyageAI API integration.
"""

import time
import math
from dataclasses import dataclass
from typing import List, Optional, Any
from collections import deque


@dataclass
class RequestRecord:
    """Represents a single API request record."""

    timestamp: float
    token_count: int
    latency: float


class RateLimiter:
    """
    Manages API rate limits using a sliding window approach.

    Limits:
    - 3 requests per minute (RPM)
    - 10,000 tokens per minute (TPM)
    """

    def __init__(
        self,
        max_requests_per_minute: int = 3,
        max_tokens_per_minute: int = 10000,
        window_size_seconds: int = 60,
        min_payload_threshold: int = 100,
    ):
        """
        Initialize the RateLimiter.

        Args:
            max_requests_per_minute: Maximum requests allowed per minute
            max_tokens_per_minute: Maximum tokens allowed per minute
            window_size_seconds: Size of the sliding window in seconds
            min_payload_threshold: Minimum payload size to send (optional)
        """
        self.max_requests_per_minute = max_requests_per_minute
        self.max_tokens_per_minute = max_tokens_per_minute
        self.window_size_seconds = window_size_seconds
        self.min_payload_threshold = min_payload_threshold

        # State variables
        self.request_history: List[RequestRecord] = []
        self.total_requests = 0
        self.total_tokens = 0
        self.start_time = time.time()

    def _clean_history(self) -> None:
        """Remove requests older than the window size to keep the list clean."""
        now = time.time()
        window_start = now - self.window_size_seconds
        self.request_history = [
            req for req in self.request_history if req.timestamp >= window_start
        ]

    def _get_active_requests(self) -> List[RequestRecord]:
        """
        Get requests strictly within the last window seconds.

        Returns:
            List of RequestRecord objects within the window
        """
        now = time.time()
        window_start = now - self.window_size_seconds
        active = [req for req in self.request_history if req.timestamp >= window_start]
        return active

    def calculate_optimal_chunk_size(self) -> int:
        """
        Calculates the ideal chunk size based on remaining budget.

        Uses the balanced strategy: distribute remaining tokens evenly
        across remaining request slots.

        Returns:
            Optimal chunk size in tokens, or -1 if we need to wait
        """
        active_requests = self._get_active_requests()
        requests_used = len(active_requests)
        tokens_used = sum(req.token_count for req in active_requests)

        remaining_requests = self.max_requests_per_minute - requests_used
        remaining_tokens = self.max_tokens_per_minute - tokens_used

        # Edge Case: If we are at the limit, we cannot send anything yet
        if remaining_requests <= 0 or remaining_tokens <= 0:
            return -1  # Indicates we need to wait

        # BALANCED STRATEGY LOGIC:
        # Distribute remaining tokens evenly across remaining request slots
        target_size = math.floor(remaining_tokens / remaining_requests)

        # Optional Safety: Don't send tiny payloads if the API latency overhead isn't worth it
        # Uncomment if you want this behavior:
        # if target_size < self.min_payload_threshold:
        #     return self.min_payload_threshold

        return target_size

    def get_wait_time(self, proposed_tokens: int) -> float:
        """
        Calculates exact sleep time needed to respect RPM and TPM.

        Args:
            proposed_tokens: The number of tokens in the proposed request

        Returns:
            Wait time in seconds (0 if no wait needed)
        """
        active_requests = self._get_active_requests()

        # If history is empty, no wait needed
        if len(active_requests) == 0:
            return 0.0

        # Sort by time ascending to find the oldest requests
        active_requests.sort(key=lambda req: req.timestamp)

        current_request_count = len(active_requests)
        current_token_count = sum(req.token_count for req in active_requests)

        # Check immediate availability
        if (
            current_request_count < self.max_requests_per_minute
            and (current_token_count + proposed_tokens) <= self.max_tokens_per_minute
        ):
            return 0.0

        # Calculate time needed to clear constraints
        max_wait_time = 0.0
        now = time.time()

        # 1. RPM Constraint: Wait until the oldest request + 60s
        if current_request_count >= self.max_requests_per_minute:
            oldest_request_time = active_requests[0].timestamp
            time_to_free_request = (
                oldest_request_time + self.window_size_seconds
            ) - now
            max_wait_time = max(max_wait_time, time_to_free_request)

        # 2. TPM Constraint: Wait until we have enough token space
        # We iterate from oldest to newest, subtracting their tokens from our usage
        # until we have enough room for 'proposedTokens'.
        if (current_token_count + proposed_tokens) > self.max_tokens_per_minute:
            tokens_to_free = (
                current_token_count + proposed_tokens
            ) - self.max_tokens_per_minute
            running_token_freed = 0

            for req in active_requests:
                running_token_freed += req.token_count

                # Once we've hypothetically freed enough tokens, check when that request expires
                if running_token_freed >= tokens_to_free:
                    time_to_free_tokens = (
                        req.timestamp + self.window_size_seconds
                    ) - now
                    max_wait_time = max(max_wait_time, time_to_free_tokens)
                    break

        # Ensure we don't return negative values due to timing precision
        return max(0.0, max_wait_time)

    def count_tokens(self, text: str) -> int:
        """
        Count tokens in text. This is a placeholder - in real implementation,
        you would use the actual tokenizer from your voyage-mcp.py file.

        Args:
            text: The text to count tokens for

        Returns:
            Estimated token count
        """
        # Character-based estimate (as fallback in original code)
        return max(1, math.ceil(len(text) / 5))

    def display_metrics(
        self, req: RequestRecord, active_req_count: int, active_token_count: int
    ) -> None:
        """
        Displays metrics to console/logs.

        Args:
            req: The request record for the current request
            active_req_count: Number of active requests in the window
            active_token_count: Number of tokens in active requests
        """
        elapsed_time_min = (time.time() - self.start_time) / 60.0
        if elapsed_time_min == 0:
            elapsed_time_min = 0.001

        avg_rpm = self.total_requests / elapsed_time_min
        avg_tpm = self.total_tokens / elapsed_time_min
        efficiency = (avg_tpm / self.max_tokens_per_minute) * 100

        print(f"--- Request #{self.total_requests} Complete ---")
        print(f"Latency:     {req.latency:.2f}s")
        print(f"Payload:     {req.token_count} tokens")
        print(
            f"Window Load: {active_req_count}/{self.max_requests_per_minute} reqs | {active_token_count}/{self.max_tokens_per_minute} tokens"
        )
        print(
            f"Perf:        {avg_rpm:.2f} RPM | {avg_tpm:.2f} TPM ({efficiency:.1f}% eff)"
        )
        print()

    def process_chunk(
        self, chunk_data: dict, actual_tokens: int, api_post_function
    ) -> tuple[RequestRecord, Any]:
        """
        Process a single chunk through the API with rate limiting.

        Args:
            chunk_data: The data chunk to send
            actual_tokens: The actual token count of the chunk
            api_post_function: The function to call for the API POST request

        Returns:
            Tuple of (RequestRecord, api_response) containing the request details and API response
        """
        request_start_time = time.time()

        # Send API Request
        api_response = api_post_function(data=chunk_data)

        request_end_time = time.time()
        request_latency = request_end_time - request_start_time

        # Create request record
        req_record = RequestRecord(
            timestamp=request_start_time,
            token_count=actual_tokens,
            latency=request_latency,
        )

        # Update State
        self.total_requests += 1
        self.total_tokens += actual_tokens
        self.request_history.append(req_record)

        # Maintenance: Remove requests older than 60s to keep list clean
        self._clean_history()

        # Calculate current window load for display
        active_load = self.request_history  # Since we just cleaned it
        active_reqs = len(active_load)
        active_toks = sum(req.token_count for req in active_load)

        # Display metrics
        self.display_metrics(req_record, active_reqs, active_toks)

        return req_record, api_response

    def execute_upload_loop(
        self,
        data_iterator,
        get_next_chunk_function,
        count_tokens_function,
        api_post_function,
    ) -> None:
        """
        Main execution loop for uploading data with rate limiting.

        Args:
            data_iterator: Iterator object with HasMoreData() method
            get_next_chunk_function: Function to get the next chunk of data
            count_tokens_function: Function to count tokens in data
            api_post_function: Function to send API POST requests
        """
        print("Initializing Upload Process (Balanced Strategy)...")
        print()

        while hasattr(data_iterator, "has_more_data") and data_iterator.has_more_data():
            # 1. Determine the ideal chunk size for the current state
            proposed_size = self.calculate_optimal_chunk_size()

            # 2. If limits are reached, calculate wait time
            if proposed_size == -1:
                # Ask for wait time based on a hypothetical request to see when the first slot opens
                wait_time = self.get_wait_time(1)
                if wait_time > 0:
                    print(f"Rate limit reached. Sleeping for {round(wait_time, 2)}s...")
                    time.sleep(wait_time)
                # Restart loop to recalculate state after sleeping
                continue

            # 3. Calculate specific wait time for this proposed chunk size
            wait_time = self.get_wait_time(proposed_size)
            if wait_time > 0:
                print(
                    f"Throttling: Sleeping for {round(wait_time, 2)}s to optimize window..."
                )
                time.sleep(wait_time)

            # 4. Prepare and Send Request
            request_start_time = time.time()

            # Get data chunk limited to our calculated target
            chunk_data = get_next_chunk_function(data_iterator, limit=proposed_size)

            # Measure actual output
            actual_tokens = count_tokens_function(chunk_data)

            # Send API Request
            api_response = api_post_function(data=chunk_data)

            # Optional: Handle API response for errors or extract embeddings
            # For example with VoyageAI/LanceDB, you might want to check:
            # if api_response and api_response.get("error"):
            #     print(f"API Error: {api_response['error']}")
            #     # Handle error (retry, log, etc.)

            request_end_time = time.time()
            request_latency = request_end_time - request_start_time

            # 5. Update State
            self.total_requests += 1
            self.total_tokens += actual_tokens

            req_record = RequestRecord(
                timestamp=request_start_time,
                token_count=actual_tokens,
                latency=request_latency,
            )
            self.request_history.append(req_record)

            # Maintenance: Remove requests older than 60s to keep list clean
            self._clean_history()

            # 6. Update Metrics
            # Calculate current window load for display
            active_load = self.request_history  # Since we just cleaned it
            active_reqs = len(active_load)
            active_toks = sum(req.token_count for req in active_load)

            self.display_metrics(req_record, active_reqs, active_toks)

            # The api_response is now available for error handling or response processing
            # Example: You could check for errors here:
            # if api_response.get("error"):
            #     print(f"API Error: {api_response['error']}")

        print("Process Finished.")


# Example usage with LanceDB/VoyageAI integration
class VoyageAIRateLimitedUploader:
    """
    Example integration class for VoyageAI API with rate limiting.
    This demonstrates how to use the RateLimiter with LanceDB and VoyageAI.
    """

    def __init__(self, rate_limiter: RateLimiter):
        """
        Initialize the uploader with a rate limiter.

        Args:
            rate_limiter: Configured RateLimiter instance
        """
        self.rate_limiter = rate_limiter

    def chunk_text(self, text: str, limit: int) -> list[str]:
        """
        Chunk text into pieces of approximately limit tokens.

        Args:
            text: The text to chunk
            limit: Maximum tokens per chunk

        Returns:
            List of text chunks
        """
        # Simple implementation - in real code, use proper tokenization
        chunks = []
        # Rough character-based chunking (5 chars per token estimate)
        chars_per_chunk = limit * 5
        for start in range(0, len(text), chars_per_chunk):
            chunks.append(text[start : start + chars_per_chunk])
        return chunks

    def api_post(self, data: dict) -> dict:
        """
        Send API POST request. This is a placeholder - in real implementation,
        this would call the VoyageAI API.

        Args:
            data: The data to send

        Returns:
            API response
        """
        # Placeholder - in real implementation, this would make the actual API call
        # For example, using voyageai client or httpx as in the original code
        return {"status": "success"}


# Example of how to use the RateLimiter with a data iterator
if __name__ == "__main__":
    # Example usage
    rate_limiter = RateLimiter(
        max_requests_per_minute=3,
        max_tokens_per_minute=10000,
        window_size_seconds=60,
        min_payload_threshold=100,
    )

    # Example data iterator
    class SimpleDataIterator:
        def __init__(self, data_chunks):
            self.chunks = data_chunks
            self.index = 0

        def has_more_data(self):
            return self.index < len(self.chunks)

        def get_next_chunk(self, limit=None):
            if self.index < len(self.chunks):
                chunk = self.chunks[self.index]
                self.index += 1
                return chunk
            return None

    # Example chunks
    example_data = [
        {"text": "This is a sample chunk of text to process."},
        {"text": "Another chunk of data for the API."},
        {"text": "Third example chunk with more content."},
        {"text": "Fourth chunk continuing the example."},
        {"text": "Final chunk for demonstration purposes."},
    ]

    data_iterator = SimpleDataIterator(example_data)

    def get_next_chunk(iterator, limit):
        return iterator.get_next_chunk(limit)

    def count_tokens(chunk):
        return rate_limiter.count_tokens(chunk.get("text", ""))

    def api_post(data):
        print(f"  -> Sending chunk: {data.get('text', '')[:30]}...")
        time.sleep(0.1)  # Simulate API latency
        return {"status": "ok"}

    # Run the upload loop
    rate_limiter.execute_upload_loop(
        data_iterator=data_iterator,
        get_next_chunk_function=get_next_chunk,
        count_tokens_function=count_tokens,
        api_post_function=api_post,
    )
