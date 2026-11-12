"""
Preview Generation Module
Creates low-resolution previews with blurred faces for user and admin review.
"""
import os
import logging
import asyncio
import tempfile
from typing import Optional, Tuple, List, Dict
from pathlib import Path
import io

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
import cv2

from storage.r2 import get_r2_storage

logger = logging.getLogger(__name__)


class PreviewGenerator:
    """Generate preview copies with quality/compression settings."""
    
    def __init__(self, max_preview_width: int = 1280, max_preview_height: int = 720,
                 video_bitrate: str = "1M", image_quality: int = 85):
        self.max_preview_width = max_preview_width
        self.max_preview_height = max_preview_height
        self.video_bitrate = video_bitrate
        self.image_quality = image_quality
    
    async def generate_video_preview(self, video_bytes: bytes, 
                                     blur_regions: Optional[List[Dict]] = None) -> bytes:
        """
        Generate video preview with optional additional blur on specified regions.
        
        Args:
            video_bytes: Original video bytes
            blur_regions: Optional list of additional regions to blur
                         [{'bbox': (x, y, w, h), 'frame_range': (start, end)}]
        
        Returns:
            bytes: Compressed preview video bytes
        """
        try:
            with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp_input:
                tmp_input.write(video_bytes)
                input_path = tmp_input.name
            
            cap = cv2.VideoCapture(input_path)
            if not cap.isOpened():
                raise RuntimeError("Cannot open video")
            
            fps = cap.get(cv2.CAP_PROP_FPS)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            
            # Resize to max preview dimensions while maintaining aspect ratio
            scale = min(self.max_preview_width / width, self.max_preview_height / height, 1.0)
            new_width = int(width * scale)
            new_height = int(height * scale)
            
            with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp_output:
                output_path = tmp_output.name
            
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(output_path, fourcc, fps, (new_width, new_height))
            
            frame_idx = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                
                # Resize frame
                resized = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA)
                
                # Apply additional blur regions if specified
                if blur_regions:
                    for region in blur_regions:
                        # Scale regions to match resized frame
                        x, y, w, h = region['bbox']
                        x = int(x * scale)
                        y = int(y * scale)
                        w = int(w * scale)
                        h = int(h * scale)
                        
                        # Check frame range
                        if 'frame_range' in region:
                            start, end = region['frame_range']
                            if not (start <= frame_idx <= end):
                                continue
                        
                        if w > 0 and h > 0:
                            roi = resized[y:y+h, x:x+w]
                            if roi.size > 0:
                                kernel_size = min(51, min(w, h) // 2 * 2 + 1)
                                if kernel_size % 2 == 0:
                                    kernel_size += 1
                                blurred_roi = cv2.GaussianBlur(roi, (kernel_size, kernel_size), 21.0)
                                resized[y:y+h, x:x+w] = blurred_roi
                
                out.write(resized)
                frame_idx += 1
            
            cap.release()
            out.release()
            
            with open(output_path, 'rb') as f:
                preview_bytes = f.read()
            
            os.unlink(input_path)
            os.unlink(output_path)
            
            logger.info(f"Generated video preview: {new_width}x{new_height}, {len(preview_bytes)} bytes")
            return preview_bytes
        
        except Exception as e:
            logger.error(f"Video preview generation failed: {e}")
            raise RuntimeError(f"Video preview generation failed: {e}")
    
    async def generate_image_preview(self, image_bytes: bytes,
                                     blur_regions: Optional[List[Dict]] = None) -> bytes:
        """
        Generate image preview with optional additional blur regions.
        
        Args:
            image_bytes: Original image bytes
            blur_regions: Optional list of regions to blur
        
        Returns:
            bytes: Compressed preview image bytes
        """
        try:
            image = Image.open(io.BytesIO(image_bytes))
            original_width, original_height = image.size
            
            # Resize if needed
            scale = min(self.max_preview_width / original_width, 
                       self.max_preview_height / original_height, 1.0)
            new_width = int(original_width * scale)
            new_height = int(original_height * scale)
            
            if scale < 1.0:
                image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # Apply additional blur regions
            if blur_regions:
                for region in blur_regions:
                    x, y, w, h = region['bbox']
                    # Scale coordinates if image was resized
                    if scale < 1.0:
                        x = int(x * scale)
                        y = int(y * scale)
                        w = int(w * scale)
                        h = int(h * scale)
                    
                    if w > 0 and h > 0:
                        face_region = image.crop((x, y, x + w, y + h))
                        blurred_region = face_region.filter(
                            ImageFilter.GaussianBlur(radius=min(w, h) // 10)
                        )
                        image.paste(blurred_region, (x, y))
            
            # Compress
            output_buffer = io.BytesIO()
            save_format = image.format or 'JPEG'
            if save_format == 'JPEG':
                image.save(output_buffer, format='JPEG', quality=self.image_quality, optimize=True)
            elif save_format == 'PNG':
                image.save(output_buffer, format='PNG', optimize=True)
            else:
                image.save(output_buffer, format='JPEG', quality=self.image_quality)
            
            preview_bytes = output_buffer.getvalue()
            logger.info(f"Generated image preview: {new_width}x{new_height}, {len(preview_bytes)} bytes")
            return preview_bytes
        
        except Exception as e:
            logger.error(f"Image preview generation failed: {e}")
            raise RuntimeError(f"Image preview generation failed: {e}")
    
    async def create_contact_sheet(self, images: List[bytes], 
                                   columns: int = 3, 
                                   thumbnail_size: Tuple[int, int] = (320, 240)) -> bytes:
        """
        Create contact sheet from multiple images.
        
        Args:
            images: List of image bytes
            columns: Number of columns in contact sheet
            thumbnail_size: (width, height) for each thumbnail
        
        Returns:
            bytes: Contact sheet image bytes
        """
        if not images:
            raise ValueError("No images provided")
        
        rows = (len(images) + columns - 1) // columns
        sheet_width = columns * thumbnail_size[0]
        sheet_height = rows * thumbnail_size[1]
        
        contact_sheet = Image.new('RGB', (sheet_width, sheet_height), (240, 240, 240))
        
        for idx, img_bytes in enumerate(images):
            row = idx // columns
            col = idx % columns
            
            try:
                thumb = Image.open(io.BytesIO(img_bytes))
                thumb.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)
                
                # Center thumbnail in cell
                x = col * thumbnail_size[0] + (thumbnail_size[0] - thumb.width) // 2
                y = row * thumbnail_size[1] + (thumbnail_size[1] - thumb.height) // 2
                
                contact_sheet.paste(thumb, (x, y))
            except Exception as e:
                logger.warning(f"Failed to add image {idx} to contact sheet: {e}")
        
        output_buffer = io.BytesIO()
        contact_sheet.save(output_buffer, format='JPEG', quality=90)
        return output_buffer.getvalue()
    
    async def upload_previews(self, submission_id: str, 
                             preview_bytes: bytes,
                             r2_path: str) -> str:
        """
        Upload preview to R2 storage.
        
        Args:
            submission_id: UUID of submission
            preview_bytes: Preview file bytes
            r2_path: R2 path within processing bucket
        
        Returns:
            str: Full R2 URI
        """
        r2 = get_r2_storage()
        return r2.upload_processing(submission_id, r2_path, preview_bytes)


def generate_thumbnail(video_bytes: bytes, timestamp_seconds: float = 1.0) -> bytes:
    """
    Extract thumbnail from video at specified timestamp.
    
    Args:
        video_bytes: Video file bytes
        timestamp_seconds: Time in seconds to extract frame
    
    Returns:
        bytes: JPEG thumbnail bytes
    """
    try:
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp_video:
            tmp_video.write(video_bytes)
            video_path = tmp_video.name
        
        cap = cv2.VideoCapture(video_path)
        cap.set(cv2.CAP_PROP_POS_MSEC, timestamp_seconds * 1000)
        ret, frame = cap.read()
        cap.release()
        
        os.unlink(video_path)
        
        if ret:
            # Convert to PIL and compress
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(frame_rgb)
            image.thumbnail((640, 480), Image.Resampling.LANCZOS)
            
            output_buffer = io.BytesIO()
            image.save(output_buffer, format='JPEG', quality=85)
            return output_buffer.getvalue()
        
        raise RuntimeError("Cannot extract frame from video")
    
    except Exception as e:
        logger.error(f"Thumbnail generation failed: {e}")
        raise RuntimeError(f"Thumbnail generation failed: {e}")