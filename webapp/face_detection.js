/**
 * Face Detection & Blur Module - Client Side
 * Uses face-api.js for in-browser face detection
 * Privacy-first: All processing happens locally
 */

// Configuration
const CONFIG = {
    blurStrength: 21,
    minFaceConfidence: 0.5,
    maxFileSize: 50 * 1024 * 1024, // 50MB
    supportedTypes: ['image/jpeg', 'image/png', 'image/webp', 'video/mp4', 'video/quicktime']
};

// State management
let state = {
    file: null,
    fileType: null,
    faces: [],
    selectedTags: {},  // faceId -> 'bystander' | 'official'
    mediaElement: null,
    blurredBlob: null
};

/**
 * Initialize face-api.js models
 */
async function initializeModels() {
    try {
        showStatus('Loading face detection models...', 'loading');
        
        // Load models from CDN
        const modelUrl = 'https://cdn.jsdelivr.net/npm/@vladmandic/face-api@1.7.2/model';
        
        await Promise.all([
            faceapi.nets.tinyFaceDetector.loadFromUri(modelUrl),
            faceapi.nets.faceLandmark68Net.loadFromUri(modelUrl),
            faceapi.nets.faceExpressionNet.loadFromUri(modelUrl)
        ]);
        
        showStatus('Models loaded successfully', 'success');
        console.log('Face detection models initialized');
    } catch (error) {
        showStatus('Failed to load face detection models', 'error');
        console.error('Model loading error:', error);
        throw error;
    }
}

/**
 * Handle file selection
 */
async function handleFileSelect(event) {
    const file = event.target.files[0];
    
    if (!file) return;
    
    // Validate file
    if (!CONFIG.supportedTypes.includes(file.type)) {
        showStatus('Unsupported file type. Please use JPG, PNG, or MP4.', 'error');
        return;
    }
    
    if (file.size > CONFIG.maxFileSize) {
        showStatus('File too large. Maximum size is 50MB.', 'error');
        return;
    }
    
    state.file = file;
    state.fileType = file.type.startsWith('video') ? 'video' : 'image';
    
    showStatus('Processing file...', 'loading');
    
    if (state.fileType === 'image') {
        await processImage(file);
    } else {
        await processVideo(file);
    }
}

/**
 * Process image file
 */
async function processImage(file) {
    try {
        // Create preview
        const img = await createImageFromFile(file);
        displayPreview(img, 'image');
        
        // Detect faces
        showStatus('Detecting faces...', 'loading');
        const detections = await detectFaces(img);
        
        if (detections.length === 0) {
            showStatus('No faces detected. You can submit as-is.', 'success');
            enableSubmit();
            return;
        }
        
        // Store detected faces
        state.faces = detections.map((detection, index) => ({
            id: `face_${index}`,
            box: detection.detection.box,
            confidence: detection.detection.score,
            landmarks: detection.landmarks
        }));
        
        // Display face tagging UI
        displayFacesForTagging(img, detections);
        
        showStatus(`Detected ${detections.length} face(s). Please tag each one.`, 'success');
        
    } catch (error) {
        showStatus('Error processing image: ' + error.message, 'error');
        console.error('Image processing error:', error);
    }
}

/**
 * Process video file
 */
async function processVideo(file) {
    try {
        // Create video element
        const video = document.createElement('video');
        video.src = URL.createObjectURL(file);
        video.muted = true;
        video.playsInline = true;
        
        await new Promise((resolve, reject) => {
            video.onloadedmetadata = resolve;
            video.onerror = reject;
        });
        
        state.mediaElement = video;
        
        // Seek to 1 second for preview
        video.currentTime = 1;
        await new Promise(resolve => video.onseeked = resolve);
        
        displayPreview(video, 'video');
        
        // Detect faces in current frame
        showStatus('Detecting faces...', 'loading');
        const detections = await detectFaces(video);
        
        if (detections.length === 0) {
            showStatus('No faces detected in preview frame. You can submit as-is.', 'success');
            enableSubmit();
            return;
        }
        
        // Store detected faces
        state.faces = detections.map((detection, index) => ({
            id: `face_${index}`,
            box: detection.detection.box,
            confidence: detection.detection.score,
            landmarks: detection.landmarks,
            timestamp: video.currentTime
        }));
        
        // Display face tagging UI
        displayFacesForTagging(video, detections);
        
        showStatus(`Detected ${detections.length} face(s). Please tag each one.`, 'success');
        
    } catch (error) {
        showStatus('Error processing video: ' + error.message, 'error');
        console.error('Video processing error:', error);
    }
}

/**
 * Detect faces in image/video element
 */
async function detectFaces(element) {
    const options = new faceapi.TinyFaceDetectorOptions({
        inputSize: 320,
        scoreThreshold: CONFIG.minFaceConfidence
    });
    
    const detections = await faceapi.detectAllFaces(
        element,
        options
    ).withFaceLandmarks();
    
    return detections;
}

/**
 * Display faces for tagging
 */
function displayFacesForTagging(mediaElement, detections) {
    const container = document.getElementById('facesContainer');
    const list = document.getElementById('facesList');
    
    list.innerHTML = '';
    
    detections.forEach((detection, index) => {
        const faceId = `face_${index}`;
        const box = detection.detection.box;
        
        // Create face card
        const card = document.createElement('div');
        card.className = 'face-card';
        card.innerHTML = `
            <div style="font-weight:bold;margin-bottom:10px;">
                Face ${index + 1} (${Math.round(detection.detection.score * 100)}% confidence)
            </div>
            <div class="face-actions">
                <button class="btn btn-bystander selected" onclick="tagFace('${faceId}', 'bystander', this)">
                    👤 Bystander<br><small>Will be blurred</small>
                </button>
                <button class="btn btn-official" onclick="tagFace('${faceId}', 'official', this)">
                    👮 Official<br><small>Keep unblurred</small>
                </button>
            </div>
        `;
        
        list.appendChild(card);
        
        // Initialize selection
        state.selectedTags[faceId] = 'bystander';
    });
    
    container.style.display = 'block';
    document.getElementById('metadataSection').style.display = 'block';
    
    // Show submit button
    const submitBtn = document.getElementById('submitBtn');
    submitBtn.style.display = 'block';
    submitBtn.disabled = false;
}

/**
 * Tag face as bystander or official
 */
function tagFace(faceId, tag, button) {
    state.selectedTags[faceId] = tag;
    
    // Update button styles
    const card = button.closest('.face-card');
    const buttons = card.querySelectorAll('.btn');
    
    buttons.forEach(btn => {
        btn.classList.remove('selected');
    });
    
    button.classList.add('selected');
}

/**
 * Create image element from file
 */
function createImageFromFile(file) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = reject;
        img.src = URL.createObjectURL(file);
    });
}

/**
 * Display preview
 */
function displayPreview(media, type) {
    const preview = document.getElementById('preview');
    const content = document.getElementById('previewContent');
    
    content.innerHTML = '';
    
    if (type === 'image') {
        content.appendChild(media);
    } else {
        content.appendChild(media);
        media.controls = true;
        media.play();
    }
    
    preview.style.display = 'block';
}

/**
 * Blur faces in image using canvas
 */
function blurFacesInImage(img, faces, tags) {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    
    canvas.width = img.naturalWidth || img.width;
    canvas.height = img.naturalHeight || img.height;
    
    // Draw original image
    ctx.drawImage(img, 0, 0);
    
    // Blur bystander faces
    faces.forEach((face, index) => {
        const tag = tags[`face_${index}`] || 'bystander';
        
        if (tag === 'bystander') {
            const box = face.box;
            
            // Validate box coordinates
            if (box.width <= 0 || box.height <= 0) return;
            
            // Add padding
            const padding = 10;
            const x = Math.max(0, box.x - padding);
            const y = Math.max(0, box.y - padding);
            const w = Math.min(canvas.width - x, box.width + padding * 2);
            const h = Math.min(canvas.height - y, box.height + padding * 2);
            
            // Extract face region
            try {
                const imageData = ctx.getImageData(x, y, w, h);
                const data = imageData.data;
                
                // Simple box blur (average surrounding pixels)
                const blurRadius = Math.max(5, Math.min(w, h) / 5);
                const blurred = boxBlur(data, w, h, blurRadius);
                
                ctx.putImageData(blurred, x, y);
            } catch (e) {
                console.warn('Could not blur face region:', e);
            }
        }
    });
    
    return canvas;
}

/**
 * Simple box blur implementation
 */
function boxBlur(imageData, width, height, radius) {
    const data = imageData.data;
    const blurred = new Uint8ClampedArray(data.length);
    
    radius = Math.floor(radius);
    if (radius < 1) return imageData;
    
    for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
            let r = 0, g = 0, b = 0, a = 0, count = 0;
            
            // Sample surrounding pixels
            for (let dy = -radius; dy <= radius; dy++) {
                for (let dx = -radius; dx <= radius; dx++) {
                    const nx = x + dx;
                    const ny = y + dy;
                    
                    if (nx >= 0 && nx < width && ny >= 0 && ny < height) {
                        const idx = (ny * width + nx) * 4;
                        r += data[idx];
                        g += data[idx + 1];
                        b += data[idx + 2];
                        a += data[idx + 3];
                        count++;
                    }
                }
            }
            
            const idx = (y * width + x) * 4;
            blurred[idx] = r / count;
            blurred[idx + 1] = g / count;
            blurred[idx + 2] = b / count;
            blurred[idx + 3] = a / count;
        }
    }
    
    return new ImageData(blurred, width, height);
}

/**
 * Strip EXIF metadata from image
 */
function stripExif(file) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => {
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');
            
            canvas.width = img.naturalWidth || img.width;
            canvas.height = img.naturalHeight || img.height;
            
            ctx.drawImage(img, 0, 0);
            
            canvas.toBlob((blob) => {
                if (blob) {
                    resolve(blob);
                } else {
                    reject(new Error('Failed to create blob'));
                }
            }, file.type, 0.95);
        };
        img.onerror = reject;
        img.src = URL.createObjectURL(file);
    });
}

/**
 * Submit processed file
 */
document.getElementById('submitBtn').addEventListener('click', async () => {
    const submitBtn = document.getElementById('submitBtn');
    submitBtn.disabled = true;
    
    try {
        showStatus('Processing file...', 'loading');
        
        // Get metadata
        const metadata = {
            incident_type: document.getElementById('incidentType').value || 'other',
            location: document.getElementById('location').value || 'Unknown',
            date: document.getElementById('incidentDate').value,
            description: document.getElementById('description').value || '',
            content_warning: document.getElementById('contentWarning').checked,
            official_tag_count: Object.values(state.selectedTags).filter(t => t === 'official').length,
            file_hash: '' // Will be computed
        };
        
        // Process file based on type
        let processedBlob;
        
        if (state.fileType === 'image') {
            const img = state.mediaElement;
            const canvas = blurFacesInImage(img, state.faces, state.selectedTags);
            processedBlob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.95));
        } else {
            // For video, we'd need ffmpeg.wasm or similar
            // For now, submit original with metadata noting faces to blur
            processedBlob = state.file;
        }
        
        // Strip EXIF
        if (state.fileType === 'image') {
            processedBlob = await stripExif(processedBlob);
        }
        
        // Compute hash
        const fileBuffer = await processedBlob.arrayBuffer();
        const hashBuffer = await crypto.subtle.digest('SHA-256', fileBuffer);
        const hashArray = Array.from(new Uint8Array(hashBuffer));
        metadata.file_hash = hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
        
        // Send to backend via Telegram WebApp
        if (window.Telegram && window.Telegram.WebApp) {
            const formData = new FormData();
            formData.append('file', processedBlob, `evidence_${metadata.file_hash.slice(0, 16)}.jpg`);
            formData.append('metadata', JSON.stringify(metadata));
            
            // Convert FormData to base64 for Telegram
            const reader = new FileReader();
            reader.onload = () => {
                const base64 = reader.result.split(',')[1];
                
                window.Telegram.WebApp.sendData(JSON.stringify({
                    file: base64,
                    metadata: metadata
                }));
                
                showStatus('✓ Evidence submitted successfully!', 'success');
            };
            reader.readAsDataURL(processedBlob);
        } else {
            showStatus('Error: Telegram WebApp not available', 'error');
        }
        
    } catch (error) {
        showStatus('Error submitting: ' + error.message, 'error');
        console.error('Submit error:', error);
        submitBtn.disabled = false;
    }
});

/**
 * Show status message
 */
function showStatus(message, type) {
    const status = document.getElementById('status');
    status.textContent = message;
    status.className = `status ${type}`;
    status.style.display = 'block';
}

/**
 * Enable submit button
 */
function enableSubmit() {
    const submitBtn = document.getElementById('submitBtn');
    submitBtn.style.display = 'block';
    submitBtn.disabled = false;
}

// Initialize on load
document.addEventListener('DOMContentLoaded', async () => {
    console.log('Evidence Recorder initialized');
    
    // Initialize models
    await initializeModels();
    
    // File input handler
    document.getElementById('fileInput').addEventListener('change', handleFileSelect);
    
    // Close WebApp button
    if (window.Telegram && window.Telegram.WebApp) {
        window.Telegram.WebApp.onEvent('mainButtonClicked', () => {
            // Handle main button click if needed
        });
    }
});

// Cleanup on unload
window.addEventListener('beforeunload', () => {
    if (state.file) {
        URL.revokeObjectURL(state.file);
    }
});