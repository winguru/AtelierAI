---
description: "Use when performing code reviews, evaluating PRs, or auditing changes. Covers clarity, consistency, naming, performance, security, testing, error handling, UI, and documentation standards."
---

# Code Review Checklist

Ensure changes align with project standards. Consult **AGENTS.md** for project-specific guidelines and use supplemental `.md` / `.spec` files for context.

## 1. Project Understanding
- Understand the purpose of changes and how they fit the overall project.
- Ask for clarification on unclear parts before reviewing.
- Review related `.spec` files for API endpoint contracts (parameters, input, output).
- Review related web pages and preferences for UI/configuration implications.

## 2. Clarity and Readability
- Variable, function, and comment names must clearly convey purpose.
- Break complex logic into simpler, manageable pieces.
- Line length: aim for ≤ 100 characters; use consistent indentation.
- `.md` files: clear headings, bullet points, code blocks.
- `.spec` files: clear endpoint descriptions, expected parameters, sample I/O.
- JSON files: indented, sorted keys for readability (`json.dumps(d, indent=2, sort_keys=True)`).

## 3. Consistency
- Match existing project style (indentation, spacing, naming).
- Reuse existing utilities and libraries; don't introduce duplicates.
- Web pages: reuse existing components and styles.
- New features should fit existing architecture and design patterns.

## 4. User Interface
- Clear, concise text on all UI elements.
- Icons and buttons must have labels or `title`/`aria-label` tooltips.
- Truncate long strings in output (e.g., first 100 chars) to keep UI clean.
- Display dicts/JSON with indentation and sorted keys.

## 5. Naming Conventions
- Descriptive names; avoid ambiguous names like `data`.
- camelCase for variables/functions, PascalCase for classes.
- Underscore prefix for private members (`_internal`).
- Boolean names imply true/false: `isActive`, `hasPermission`, `shouldUpdate`.
- Plural for collections (`users`), singular for items (`user`).
- Avoid abbreviations unless widely understood in project context.

## 6. Functionality
- Changes achieve intended functionality without introducing bugs.
- Consider integration with existing features; avoid conflicts and side effects.
- Document current and planned functionality in `.md` files as appropriate.

## 7. Performance
- Avoid unnecessary resource consumption; optimize without sacrificing readability.
- Cache search results or expensive computations when accessed repeatedly.
- **HTTP 203**: return when serving cached data that may be stale.
- **HTTP 304**: return when client's cached version is still valid.
- Implement appropriate cache invalidation (expiration, busting on data change).

## 8. Security
- Validate user input.
- Handle sensitive data appropriately.
- Follow security best practices; no secrets in logs or error messages.

## 9. Testing
- Tests cover edge cases and failure points in separate test files.
- Test HTTP 4xx/5xx responses; ensure failed requests are logged.
- Throttle API requests; implement retry with exponential backoff.
- Test empty, missing, and malformed input (including API responses) gracefully.

## 10. Documentation
- New functions, classes, and modules must have docstrings.
- Complex logic gets inline comments explaining the "why."

## 11. Error Handling
- Graceful exception handling with informative messages.
- Use `debug`/`verbose` flags to control log verbosity.
- Full I/O logging when debug mode is enabled.
- Log at the appropriate level (error, warning, info) based on severity.
- No sensitive info in error messages or logs.
- Handle network issues, timeouts, and unexpected API responses.
- Consider centralized error handling for consistency.
