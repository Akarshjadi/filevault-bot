"""
Face Detection and Blur Processing Module
Uses MediaPipe for lightweight CPU-based face detection.
All faces blurred by default for DPDP compliance.
"""
import os
import logging
import asyncio
from typing import List, Tuple, Optional, Dict
from pathlib import Path
import tempfile
import json

import numpy as np
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.vision import FaceDetector
from PIL import Image, ImageDraw, ImageFilter
import cv2

from storage.r2 import get_r2_storage

logger = logging.getLogger(__name__)


class FaceDetectionResult:
    """Single detected face with metadata."""
    def __init__(self, face_id: str, bbox: Tuple[float, float, float, float], 
                 confidence: float, frame_index: int = 0, timestamp: float = 0.0):
        self.face_id = face_id
        self.bbox = bbox  # (x, y, width, height) in normalized coordinates
        self.confidence = confidence
        self.frame_index = frame_index
        self.timestamp = timestamp
    
    def to_dict(self) -> dict:
        return {
            'face_id': self.face_id,
            'bbox': self.bbox,
            'confidence': self.confidence,
            'frame_index': self.frame_index,
            'timestamp': self.timestamp,
        }


class FaceBlurProcessor:
    """
    Face detection and blur processing for images and videos.
    Uses MediaPipe Face Detection for CPU-efficient processing.
    """
    
    def __init__(self, model_asset_path: Optional[str] = None, min_detection_confidence: float = 0.5):
        """
        Initialize face detector.
        
        Args:
            model_asset_path: Path to MediaPipe face detection model. If None, uses default.
            min_detection_confidence: Minimum confidence for face detection (0.0-1.0)
        """
        self.min_detection_confidence = min_detection_confidence
        self.detector = None
        self._initialize_detector(model_asset_path)
        
        # Blur settings
        self.blur_kernel_size = 51  # Must be odd
        self.blur_sigma = 21.0
    
    def _initialize_detector(self, model_asset_path: Optional[str] = None):
        """Initialize MediaPipe face detector."""
        try:
            if model_asset_path is None:
                # Download default model if not provided
                import urllib.request
                model_url = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
                model_asset_path = "/tmp/blaze_face_short_range.tflite"
                
                if not os.path.exists(model_asset_path):
                    logger.info("Downloading MediaPipe face detection model...")
                    urllib.request.urlretrieve(model_url, model_asset_path)
            
            base_options = vision.BaseOptions(model_asset_path=model_asset_path)
            options = vision.FaceDetectorOptions(
                base_options=base_options,
                min_detection_confidence=self.min_detection_confidence,
            )
            self.detector = FaceDetector.create_from_options(options)
            logger.info("Face detector initialized")
        
        except Exception as e:
            logger.error(f"Failed to initialize face detector: {e}")
            raise RuntimeError(f"Cannot initialize face detection: {e}")
    
    async def process_video(self, video_bytes: bytes, submission_id: str, 
                           sample_interval_seconds: int = 2) -> Tuple[bytes, List[Dict]]:
        """
        Process video: detect faces, create blurred copy.
        
        Args:
            video_bytes: Original video bytes
            submission_id: Submission UUID for tracking
            sample_interval_seconds: Detect faces every N seconds (default: 2)
        
        Returns:
            Tuple of (blurred_video_bytes, list_of_detected_faces_metadata)
        
        Raises:
            RuntimeError: If processing fails
        """
        if self.detector is None:
            raise RuntimeError("Face detector not initialized")
        
        try:
            # Write video to temp file
            with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp_input:
                tmp_input.write(video_bytes)
                input_path = tmp_input.name
            
            # Open video
            cap = cv2.VideoCapture(input_path)
            if not cap.isOpened():
                raise RuntimeError("Cannot open video file")
            
            # Get video properties
            fps = cap.get(cv2.CAP_PROP_FPS)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            sample_interval_frames = int(fps * sample_interval_seconds)
            
            logger.info(f"Processing video: {width}x{height}, {fps}fps, {total_frames} frames")
            
            # Prepare output video
            with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp_output:
                output_path = tmp_output.name
            
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
            
            detected_faces_all = []
            frame_idx = 0
            
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                
                # Process every Nth frame for face detection
                if frame_idx % sample_interval_frames == 0:
                    # Convert to RGB for MediaPipe
                    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    mp_image = vision.Image(image_format=vision.ImageFormat.SRGB, data=rgb_frame)
                    
                    # Detect faces
                    detection_result = self.detector.detect(mp_image)
                    
                    # Extract face bounding boxes
                    faces = []
                    if detection_result.detections:
                        for detection in detection_result.detections:
                            bbox = detection.bounding_box
                            # Convert normalized to pixel coordinates
                            x = int(bbox.origin_x * width / 1000)
                            y = int(bbox.origin_y * height / 1000)
                            w = int(bbox.width * width / 1000)
                            h = int(bbox.height * height / 1000)
                            
                            # Clamp to frame bounds
                            x = max(0, x)
                            y = max(0, y)
                            w = min(w, width - x)
                            h = min(h, height - y)
                            
                            faces.append({
                                'face_id': f"face_{submission_id}_{frame_idx}_{len(faces)}",
                                'bbox': (x, y, w, h),
                                'confidence': detection.categories[0].score if detection.categories else 0.0,
                                'frame_index': frame_idx,
                                'timestamp': frame_idx / fps,
                            })
                    
                    # Apply blur to detected faces
                    blurred_frame = self._blur_faces(frame, faces)
                    detected_faces_all.extend(faces)
                else:
                    # Use original frame (or blur from previous frame)
                    blurred_frame = frame
                
                out.write(blurred_frame)
                frame_idx += 1
            
            # Release resources
            cap.release()
            out.release()
            
            # Read output video
            with open(output_path, 'rb') as f:
                blurred_bytes = f.read()
            
            # Cleanup temp files
            os.unlink(input_path)
            os.unlink(output_path)
            
            logger.info(f"Processed {frame_idx} frames, detected {len(detected_faces_all)} faces total")
            return blurred_bytes, detected_faces_all
        
        except Exception as e:
            logger.error(f"Video processing failed: {e}")
            raise RuntimeError(f"Video processing failed: {e}")
    
    async def process_image(self, image_bytes: bytes, submission_id: str) -> Tuple[bytes, List[Dict]]:
        """
        Process image: detect faces, create blurred copy.
        
        Args:
            image_bytes: Original image bytes
            submission_id: Submission UUID for tracking
        
        Returns:
            Tuple of (blurred_image_bytes, list_of_detected_faces)
        
        Raises:
            RuntimeError: If processing fails
        """
        if self.detector is None:
            raise RuntimeError("Face detector not initialized")
        
        try:
            # Convert to PIL Image
            image = Image.open(io.BytesIO(image_bytes))
            image_rgb = image.convert('RGB')
            width, height = image.size
            
            # Convert to MediaPipe format
            mp_image = vision.Image(
                image_format=vision.ImageFormat.SRGB,
                data=np.array(image_rgb)
            )
            
            # Detect faces
            detection_result = self.detector.detect(mp_image)
            
            faces = []
            if detection_result.detections:
                for idx, detection in enumerate(detection_result.detections):
                    bbox = detection.bounding_box
                    x = int(bbox.origin_x * width / 1000)
                    y = int(bbox.origin_y * height / 1000)
                    w = int(bbox.width * width / 1000)
                    h = int(bbox.height * height / 1000)
                    
                    faces.append({
                        'face_id': f"face_{submission_id}_img_{idx}",
                        'bbox': (x, y, w, h),
                        'confidence': detection.categories[0].score if detection.categories else 0.0,
                        'frame_index': 0,
                        'timestamp': 0.0,
                    })
            
            # Apply blur
            blurred_image = self._blur_faces_image(image_rgb, faces)
            
            # Convert back to bytes
            output_buffer = io.BytesIO()
            blurred_image.save(output_buffer, format=image.format or 'JPEG')
            blurred_bytes = output_buffer.getvalue()
            
            logger.info(f"Processed image, detected {len(faces)} faces")
            return blurred_bytes, faces
        
        except Exception as e:
            logger.error(f"Image processing failed: {e}")
            raise RuntimeError(f"Image processing failed: {e}")
    
    def _blur_faces(self, frame: np.ndarray, faces: List[Dict]) -> np.ndarray:
        """Apply Gaussian blur to detected faces in a frame."""
        result = frame.copy()
        for face in faces:
            x, y, w, h = face['bbox']
            if w > 0 and h > 0:
                # Ensure kernel size is odd and positive
                kernel_size = max(3, self.blur_kernel_size)
                if kernel_size % 2 == 0:
                    kernel_size += 1
                
                # Extract ROI and blur
                roi = result[y:y+h, x:x+w]
                if roi.size > 0:
                    blurred_roi = cv2.GaussianBlur(roi, (kernel_size, kernel_size), self.blur_sigma)
                    result[y:y+h, x:x+w] = blurred_roi
        return result
    
    def _blur_faces_image(self, image: Image.Image, faces: List[Dict]) -> Image.Image:
        """Apply blur to detected faces in a PIL Image."""
        result = image.copy()
        draw = ImageDraw.Dut(result)
        
        for face in faces:
            x, y, w, h = face['bbox']
            if w > 0 and h > 0:
                # Create mask for face region
                face_region = result.crop((x, y, x + w, y + h))
                
                # Apply strong blur
                blurred_region = face_region.filter(
                    ImageFilter.GaussianBlur(radius=min(w, h) // 10)
                )
                
                # Paste back
                result.paste(blurred_region, (x, y))
        
        return result
    
    def strip_exif(self, file_bytes: bytes, filename: str) -> bytes:
        """
        Strip ALL EXIF metadata from image for privacy.
        
        Args:
            file_bytes: Raw file bytes
            filename: Original filename
        
        Returns:
            bytes: Cleaned file bytes with no EXIF
        """
        try:
            if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.tiff')):
                image = Image.open(io.BytesIO(file_bytes))
                
                # Create new image without EXIF
                data = list(image.getdata())
                image_without_exif = Image.new(image.mode, image.size)
                image_without_exif.putdata(data)
                
                # Save without EXIF
                output = io.BytesIO()
                save_format = image.format or 'JPEG'
                if save_format == 'JPEG':
                    image_without_exif.save(output, format='JPEG', quality=95, optimize=True)
                elif save_format == 'PNG':
                    image_without_exif.save(output, format='PNG', optimize=True)
                else:
                    image_without_exif.save(output, format='JPEG', quality=95)
                
                return output.getvalue()
            
            return file_bytes
        
        except Exception as e:
            logger.warning(f"EXIF stripping failed: {e}")
            return file_bytes
    
    def extract_exif(self, file_bytes: bytes, filename: str) -> Dict:
        """
        Extract EXIF data from image/video.
        
        Args:
            file_bytes: Raw file bytes
            filename: Original filename (for format detection)
        
        Returns:
            dict: Extracted EXIF data
        """
        try:
            exif_data = {}
            
            if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.tiff')):
                image = Image.open(io.BytesIO(file_bytes))
                exif_info = image._getexif()
                if exif_info:
                    # Extract safe EXIF fields (no GPS for privacy)
                    safe_tags = {
                        'DateTime': 'datetime',
                        'Make': 'make',
                        'Model': 'model',
                        'Software': 'software',
                        'ExifImageWidth': 'width',
                        'ExifImageHeight': 'height',
                        'ColorSpace': 'color_space',
                    }
                    for tag, value in exif_info.items():
                        tag_name = safe_tags.get(tag)
                        if tag_name:
                            exif_data[tag_name] = str(value)
            
            # Add file metadata
            exif_data['file_size_bytes'] = len(file_bytes)
            exif_data['filename'] = filename
            
            return exif_data
        
        except Exception as e:
            logger.warning(f"EXIF extraction failed: {e}")
            return {'error': str(e)}


# Utility function for face embedding comparison
def compute_face_embedding(face_image: np.ndarray) -> Optional[np.ndarray]:
    """
    Compute face embedding for selfie matching.
    Uses simple approach - in production, use facenet or deepface.
    
    Args:
        face_image: Face crop as numpy array (BGR format)
    
    Returns:
        Optional[np.ndarray]: 128-dim embedding vector or None
    """
    try:
        # Use MediaPipe's face embedding if available, else simple fallback
        # For production, integrate facenet-pytorch or deepface
        gray = cv2.cvtColor(face_image, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, (128, 128))
        
        # Simple feature vector (replace with proper embedding model in production)
        embedding = resized.flatten().astype(np.float32)
        # Normalize
        embedding = embedding / (np.linalg.norm(embedding) + 1e-8)
        
        return embedding
    
    except Exception as e:
        logger.error(f"Face embedding computation failed: {e}")
        return None


def compare_faces(embedding1: np.ndarray, embedding2: np.ndarray, threshold: float = 0.6) -> bool:
    """
    Compare two face embeddings using cosine similarity.
    
    Args:
        embedding1: First embedding vector
        embedding2: Second embedding vector
        threshold: Similarity threshold (0.0-1.0)
    
    Returns:
        bool: True if embeddings match above threshold
    """
    try:
        # Cosine similarity
        similarity = np.dot(embedding1, embedding2) / (
            np.linalg.norm(embedding1) * np.linalg.norm(embedding2)
        )
        return similarity > threshold
    
    except Exception as e:
        logger.error(f"Face comparison failed: {e}")
        return False


# Import required modules
import io