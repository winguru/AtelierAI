// Wait for the DOM to be fully loaded before running the script
document.addEventListener('DOMContentLoaded', () => {

    // --- POPULATE DROPDOWNS ON PAGE LOAD ---
    const artistDatalist = document.getElementById('artist-suggestions');
    const licenseSelect = document.getElementById('license-select');

    // Fetch and populate artists
    fetch('/artists/')
        .then(response => response.json())
        .then(artists => {
            artists.forEach(artist => {
                const option = document.createElement('option');
                option.value = artist.name;
                artistDatalist.appendChild(option);
            });
        });

    // Fetch and populate licenses
    fetch('/licenses/')
        .then(response => response.json())
        .then(licenses => {
            licenses.forEach(license => {
                const option = document.createElement('option');
                option.value = license.id;
                option.textContent = `${license.short_name} - ${license.name}`;
                licenseSelect.appendChild(option);
            });
        });


    // --- UPLOAD FUNCTIONALITY ---
    const uploadForm = document.getElementById('upload-form');
    const uploadOutput = document.getElementById('upload-output');

    uploadForm.addEventListener('submit', async (event) => {
        event.preventDefault(); // Prevent default form submission

        const fileInput = document.getElementById('image-files');
        const artistName = document.getElementById('artist-name').value;
        const sourceUrl = document.getElementById('source-url').value;
        const licenseId = document.getElementById('license-select').value;

        if (fileInput.files.length === 0) {
            uploadOutput.textContent = 'Error: Please select at least one image to upload.';
            return;
        }

        // Create FormData to send files and form data together
        const formData = new FormData();
        // Append each selected file
        for (const file of fileInput.files) {
            formData.append('files', file);
        }
        // Append other form fields
        if (artistName) formData.append('artist_name', artistName);
        if (sourceUrl) formData.append('source_url', sourceUrl);
        if (licenseId) formData.append('license_id', licenseId);

        // Give user feedback
        uploadOutput.textContent = 'Uploading... please wait.';
        uploadForm.querySelector('button').disabled = true;

        try {
            const response = await fetch('/upload_images/', {
                method: 'POST',
                body: formData, // No 'Content-Type' header needed, browser sets it for FormData
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || `HTTP error! Status: ${response.status}`);
            }

            const result = await response.json();
            uploadOutput.textContent = JSON.stringify(result, null, 2);
            
            // Optionally, clear the form on success
            uploadForm.reset();

        } catch (error) {
            console.error('Error uploading images:', error);
            uploadOutput.textContent = `Error: ${error.message}`;
        } finally {
            uploadForm.querySelector('button').disabled = false;
        }
    });

    // --- UPDATED SCANNING FUNCTIONALITY ---
    const rescanBtn = document.getElementById('rescan-btn');
    const scanOutput = document.getElementById('scan-output');

    rescanBtn.addEventListener('click', async () => {
        scanOutput.textContent = 'Scanning library... this may take a while.';
        rescanBtn.disabled = true;

        try {
            const response = await fetch('/rescan_library/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || `HTTP error! Status: ${response.status}`);
            }

            const result = await response.json();
            scanOutput.textContent = JSON.stringify(result, null, 2);

        } catch (error) {
            console.error('Error scanning library:', error);
            scanOutput.textContent = `Error: ${error.message}`;
        } finally {
            rescanBtn.disabled = false;
        }
    });
    
    // Get references to the HTML elements we need to interact with
    const loadImagesBtn = document.getElementById('load-images-btn');
    const apiOutput = document.getElementById('api-output');

    // Add a 'click' event listener to the button
    loadImagesBtn.addEventListener('click', async () => {
        
        // Update the button text to give the user feedback
        loadImagesBtn.textContent = 'Loading...';
        loadImagesBtn.disabled = true;

        try {
            // Make a GET request to our API endpoint
            const response = await fetch('/images/');
            
            // Check if the request was successful
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}`);
            }

            // Parse the JSON data from the response
            const images = await response.json();

            // Display the raw JSON data in the <pre> tag for now
            apiOutput.textContent = JSON.stringify(images, null, 2);

        } catch (error) {
            // If an error occurs, display it in the output area
            console.error('Error fetching images:', error);
            apiOutput.textContent = `Error: ${error.message}`;
        } finally {
            // Reset the button text and state, whether the request succeeded or failed
            loadImagesBtn.textContent = 'Load Images from API';
            loadImagesBtn.disabled = false;
        }
    });
});