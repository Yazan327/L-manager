"""
Image processor for PropertyFinder listings.
Handles image resizing, ratio enforcement, QR code overlay, and logo watermarking.
"""

from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import qrcode
import io
import base64
from typing import Tuple, Optional, List, Union
import requests
import os

# Try to import styled QR code components (optional)
try:
    from qrcode.image.styledpil import StyledPilImage
    from qrcode.image.styles.moduledrawers import RoundedModuleDrawer
    HAS_STYLED_QR = True
except ImportError:
    HAS_STYLED_QR = False
    print("[ImageProcessor] Styled QR code not available, using basic QR codes")


class ImageProcessor:
    """Process images for PropertyFinder listings."""
    
    # PropertyFinder recommended ratios
    RATIOS = {
        'pf_standard': (3, 2),         # 3:2 (1.5) - PropertyFinder optimal (1.3-1.8 range)
        'pf_wide': (16, 10),           # 16:10 (1.6) - PropertyFinder wide
        'landscape_16_9': (16, 9),     # 16:9 - Primary listing image
        'landscape_4_3': (4, 3),       # 4:3 - Standard photo
        'square': (1, 1),              # 1:1 - Thumbnail/social
        'portrait_9_16': (9, 16),      # 9:16 - Stories/vertical
        'portrait_3_4': (3, 4),        # 3:4 - Portrait photo
        'wide_21_9': (21, 9),          # 21:9 - Banner/header
    }
    
    # Recommended sizes for PropertyFinder
    SIZES = {
        'original': None,              # Keep original size
        'full_hd': (1920, 1080),       # Full HD
        'hd': (1280, 720),             # HD
        'pf_min': (800, 600),          # PropertyFinder minimum
        'medium': (1024, 768),         # Medium
        'small': (640, 480),           # Small (below PF min!)
        'thumbnail': (320, 240),       # Thumbnail
    }
    
    # QR/Logo positions
    POSITIONS = {
        'bottom_right': ('right', 'bottom'),
        'bottom_left': ('left', 'bottom'),
        'top_right': ('right', 'top'),
        'top_left': ('left', 'top'),
        'center': ('center', 'center'),
        'bottom_center': ('center', 'bottom'),
        'top_center': ('center', 'top'),
    }
    
    def __init__(self):
        """Initialize the image processor."""
        pass
    
    def load_image(self, image_source: Union[str, bytes, Image.Image]) -> Image.Image:
        """
        Load an image from various sources.
        
        Args:
            image_source: URL, file path, bytes, base64 string, or PIL Image
            
        Returns:
            PIL Image object
        """
        if isinstance(image_source, Image.Image):
            return image_source.copy()
        
        if isinstance(image_source, bytes):
            return Image.open(io.BytesIO(image_source))
        
        if isinstance(image_source, str):
            # Check if it's base64
            if image_source.startswith('data:image'):
                # Extract base64 data
                base64_data = image_source.split(',')[1]
                image_bytes = base64.b64decode(base64_data)
                return Image.open(io.BytesIO(image_bytes))
            
            # Check if it's a URL
            if image_source.startswith(('http://', 'https://')):
                response = requests.get(image_source, timeout=30)
                response.raise_for_status()
                return Image.open(io.BytesIO(response.content))
            
            # Assume it's a file path
            if os.path.exists(image_source):
                return Image.open(image_source)
        
        raise ValueError(f"Cannot load image from: {type(image_source)}")
    
    def apply_ratio(self, img: Image.Image, ratio: str, crop_position: str = 'center') -> Image.Image:
        """
        Crop image to target aspect ratio.
        
        Args:
            img: PIL Image
            ratio: Target ratio key (e.g., 'landscape_16_9', 'square')
            crop_position: Where to crop from ('center', 'top', 'bottom', 'left', 'right')
            
        Returns:
            Cropped PIL Image
        """
        if ratio not in self.RATIOS:
            return img
        
        target_ratio = self.RATIOS[ratio]
        target_aspect = target_ratio[0] / target_ratio[1]
        
        width, height = img.size
        current_aspect = width / height
        
        if abs(current_aspect - target_aspect) < 0.01:
            # Already correct ratio
            return img
        
        if current_aspect > target_aspect:
            # Image is wider - crop width
            new_width = int(height * target_aspect)
            if crop_position == 'left':
                left = 0
            elif crop_position == 'right':
                left = width - new_width
            else:  # center
                left = (width - new_width) // 2
            return img.crop((left, 0, left + new_width, height))
        else:
            # Image is taller - crop height
            new_height = int(width / target_aspect)
            if crop_position == 'top':
                top = 0
            elif crop_position == 'bottom':
                top = height - new_height
            else:  # center
                top = (height - new_height) // 2
            return img.crop((0, top, width, top + new_height))
    
    def resize_image(self, img: Image.Image, size: str = 'original', max_dimension: int = None) -> Image.Image:
        """
        Resize image to target size.
        
        Args:
            img: PIL Image
            size: Size preset key or 'original'
            max_dimension: Optional max width/height (overrides size preset)
            
        Returns:
            Resized PIL Image
        """
        if max_dimension:
            # Scale to fit within max dimension
            width, height = img.size
            if width > height:
                if width > max_dimension:
                    new_width = max_dimension
                    new_height = int(height * (max_dimension / width))
                    return img.resize((new_width, new_height), Image.LANCZOS)
            else:
                if height > max_dimension:
                    new_height = max_dimension
                    new_width = int(width * (max_dimension / height))
                    return img.resize((new_width, new_height), Image.LANCZOS)
            return img
        
        if size == 'original' or size not in self.SIZES or self.SIZES[size] is None:
            return img
        
        target_size = self.SIZES[size]
        return img.resize(target_size, Image.LANCZOS)
    
    def generate_qr_code(
        self, 
        data: str, 
        size: int = 200,
        fill_color: str = '#000000',
        back_color: str = '#FFFFFF',
        logo: Image.Image = None,
        rounded: bool = True
    ) -> Image.Image:
        """
        Generate a QR code image.
        
        Args:
            data: Data to encode in QR code
            size: Size of QR code in pixels
            fill_color: QR code foreground color
            back_color: QR code background color
            logo: Optional logo to embed in center
            rounded: Use rounded modules
            
        Returns:
            PIL Image of QR code
        """
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H if logo else qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(data)
        qr.make(fit=True)
        
        if rounded and HAS_STYLED_QR:
            qr_img = qr.make_image(
                image_factory=StyledPilImage,
                module_drawer=RoundedModuleDrawer(),
                fill_color=fill_color,
                back_color=back_color
            )
        else:
            qr_img = qr.make_image(fill_color=fill_color, back_color=back_color)
        
        # Convert to RGBA for transparency support
        qr_img = qr_img.convert('RGBA')
        
        # Resize to target size
        qr_img = qr_img.resize((size, size), Image.LANCZOS)
        
        # Add logo to center if provided
        if logo:
            logo = logo.convert('RGBA')
            logo_size = size // 4
            logo = logo.resize((logo_size, logo_size), Image.LANCZOS)
            
            # Calculate position
            logo_pos = ((size - logo_size) // 2, (size - logo_size) // 2)
            
            # Create white background for logo
            bg = Image.new('RGBA', (logo_size + 10, logo_size + 10), (255, 255, 255, 255))
            bg_pos = (logo_pos[0] - 5, logo_pos[1] - 5)
            qr_img.paste(bg, bg_pos)
            qr_img.paste(logo, logo_pos, logo)
        
        return qr_img
    
    def add_overlay(
        self,
        img: Image.Image,
        overlay: Image.Image,
        position: str = 'bottom_right',
        size_percent: int = 15,
        margin_percent: int = 3,
        opacity: float = 1.0
    ) -> Image.Image:
        """
        Add an overlay (QR code or logo) to an image.
        
        Args:
            img: Base PIL Image
            overlay: Overlay image (QR code or logo)
            position: Position key (bottom_right, top_left, etc.)
            size_percent: Overlay size as percentage of image width (5-50)
            margin_percent: Margin from edges as percentage
            opacity: Overlay opacity (0.0-1.0)
            
        Returns:
            Image with overlay
        """
        # Ensure RGBA mode
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        img = img.copy()
        width, height = img.size
        
        # Calculate overlay size
        size_percent = max(5, min(50, size_percent))
        overlay_width = int(width * size_percent / 100)
        overlay_height = int(overlay.size[1] * (overlay_width / overlay.size[0]))
        
        overlay = overlay.convert('RGBA')
        overlay = overlay.resize((overlay_width, overlay_height), Image.LANCZOS)
        
        # Apply opacity
        if opacity < 1.0:
            alpha = overlay.split()[3]
            alpha = alpha.point(lambda p: int(p * opacity))
            overlay.putalpha(alpha)
        
        # Calculate margin
        margin = int(min(width, height) * margin_percent / 100)
        
        # Calculate position
        pos_x, pos_y = self.POSITIONS.get(position, ('right', 'bottom'))
        
        if pos_x == 'left':
            x = margin
        elif pos_x == 'right':
            x = width - overlay_width - margin
        else:  # center
            x = (width - overlay_width) // 2
        
        if pos_y == 'top':
            y = margin
        elif pos_y == 'bottom':
            y = height - overlay_height - margin
        else:  # center
            y = (height - overlay_height) // 2
        
        # Paste overlay
        img.paste(overlay, (x, y), overlay)
        
        return img
    
    def process_image(
        self,
        image_source: Union[str, bytes, Image.Image],
        ratio: str = None,
        size: str = 'original',
        max_dimension: int = None,
        qr_data: str = None,
        qr_position: str = 'bottom_right',
        qr_size_percent: int = 15,
        qr_color: str = '#000000',
        qr_opacity: float = 1.0,
        logo_source: Union[str, bytes, Image.Image] = None,
        logo_position: str = 'bottom_left',
        logo_size_percent: int = 12,
        logo_opacity: float = 0.9,
        output_format: str = 'JPEG',
        quality: int = 90
    ) -> Tuple[bytes, dict]:
        """
        Process an image with all transformations.
        
        Args:
            image_source: Source image (URL, path, bytes, base64, or PIL Image)
            ratio: Target aspect ratio
            size: Target size preset
            max_dimension: Max width/height
            qr_data: Data for QR code
            qr_position: QR code position
            qr_size_percent: QR code size (% of width)
            qr_color: QR code fill color
            qr_opacity: QR code opacity
            logo_source: Logo image source
            logo_position: Logo position
            logo_size_percent: Logo size (% of width)
            logo_opacity: Logo opacity
            output_format: Output format (JPEG, PNG, WEBP)
            quality: Output quality (1-100)
            
        Returns:
            Tuple of (processed image bytes, metadata dict)
        """
        # Load image
        img = self.load_image(image_source)
        original_size = img.size
        
        # Ensure image is in a workable mode (RGB or RGBA)
        if img.mode == 'P':
            # Palette mode - convert to RGBA to preserve transparency
            img = img.convert('RGBA')
        elif img.mode == 'L':
            # Grayscale - convert to RGB
            img = img.convert('RGB')
        elif img.mode == 'LA':
            # Grayscale with alpha - convert to RGBA
            img = img.convert('RGBA')
        elif img.mode == '1':
            # Binary - convert to RGB
            img = img.convert('RGB')
        elif img.mode == 'CMYK':
            # CMYK - convert to RGB
            img = img.convert('RGB')
        
        # Apply ratio
        if ratio:
            img = self.apply_ratio(img, ratio)
        
        # Resize
        img = self.resize_image(img, size, max_dimension)
        
        # Add QR code
        if qr_data:
            qr_img = self.generate_qr_code(
                qr_data,
                size=int(img.size[0] * qr_size_percent / 100),
                fill_color=qr_color
            )
            img = self.add_overlay(img, qr_img, qr_position, qr_size_percent, opacity=qr_opacity)
        
        # Add logo
        if logo_source:
            try:
                logo_img = self.load_image(logo_source)
                img = self.add_overlay(img, logo_img, logo_position, logo_size_percent, opacity=logo_opacity)
            except Exception as e:
                print(f"Warning: Could not load logo: {e}")
        
        # Convert to RGB for JPEG
        if output_format.upper() == 'JPEG':
            if img.mode == 'RGBA':
                background = Image.new('RGB', img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3])
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
        
        # Save to bytes
        output = io.BytesIO()
        try:
            save_kwargs = {'quality': quality} if output_format.upper() in ('JPEG', 'WEBP') else {}
            img.save(output, format=output_format.upper(), **save_kwargs)
        except Exception as save_err:
            # Fallback: try saving as JPEG
            print(f"Warning: Could not save as {output_format}: {save_err}, falling back to JPEG")
            if img.mode != 'RGB':
                img = img.convert('RGB')
            img.save(output, format='JPEG', quality=quality)
            output_format = 'JPEG'
        output.seek(0)
        
        metadata = {
            'original_size': original_size,
            'final_size': img.size,
            'ratio': ratio,
            'format': output_format.upper(),
            'has_qr': bool(qr_data),
            'has_logo': bool(logo_source),
            'file_size': len(output.getvalue())
        }
        
        return output.getvalue(), metadata
    
    def process_batch(
        self,
        images: List[Union[str, bytes, Image.Image]],
        **kwargs
    ) -> List[Tuple[bytes, dict]]:
        """
        Process multiple images with the same settings.
        
        Args:
            images: List of image sources
            **kwargs: Processing options (passed to process_image)
            
        Returns:
            List of (processed bytes, metadata) tuples
        """
        results = []
        for img_source in images:
            try:
                result = self.process_image(img_source, **kwargs)
                results.append(result)
            except Exception as e:
                results.append((None, {'error': str(e)}))
        return results
    
    def image_to_base64(self, img_bytes: bytes, format: str = 'JPEG') -> str:
        """Convert image bytes to base64 data URL."""
        mime_type = f"image/{format.lower()}"
        b64 = base64.b64encode(img_bytes).decode('utf-8')
        return f"data:{mime_type};base64,{b64}"
