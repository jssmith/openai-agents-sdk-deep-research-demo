import base64
import mimetypes
import os
from dataclasses import dataclass
from io import BytesIO
from typing import Literal, Optional

from openai import AsyncOpenAI
from PIL import Image
from pydantic import BaseModel
from temporalio import activity

ImageSize = Literal["1024x1024", "1536x1024", "1024x1536", "auto"]
ImageOutputFormat = Literal["png", "jpeg", "webp"]
ImageQuality = Literal["low", "medium", "high", "auto"]


class ImageStylingOptions(BaseModel):
    """Styling options for image generation"""

    size: ImageSize = "1024x1024"
    quality: ImageQuality = "low"
    output_format: ImageOutputFormat = "jpeg"
    output_compression: Optional[int] = 65  # 0-100 for JPEG/WEBP
    resize_width: Optional[int] = 640  # Resize for report/PDF embedding


@dataclass
class ImageGenerationResult:
    """Result from image generation activity"""

    image_file_path: str | None  # Path to saved image file
    mime_type: str
    success: bool
    error_message: Optional[str] = None


@activity.defn
async def generate_image(
    prompt: str,
    styling_options: Optional[ImageStylingOptions] = None,
) -> ImageGenerationResult:
    """
    Generate an image using OpenAI's image generation API.

    Args:
        prompt: Detailed image description (2-3 sentences)
        styling_options: Optional styling configurations

    Returns:
        ImageGenerationResult with image bytes and success status
    """
    try:
        cached_image_path = os.getenv("DEMO_RESEARCH_IMAGE_PATH", "").strip()
        if cached_image_path:
            from pathlib import Path as _Path

            temp_dir = _Path("temp_images").resolve()
            requested = _Path(cached_image_path)
            abs_requested = (
                requested if requested.is_absolute() else _Path.cwd() / requested
            ).resolve()

            try:
                rel = abs_requested.relative_to(temp_dir)
                relative_path = f"temp_images/{rel.as_posix()}"
            except ValueError:
                relative_path = None

            if relative_path and abs_requested.is_file():
                mime_type = (
                    mimetypes.guess_type(str(abs_requested))[0] or "image/jpeg"
                )
                activity.logger.info(
                    f"Using cached demo research image: {relative_path}"
                )
                return ImageGenerationResult(
                    image_file_path=relative_path,
                    mime_type=mime_type,
                    success=True,
                )
            if not relative_path:
                activity.logger.warning(
                    "DEMO_RESEARCH_IMAGE_PATH must point to a file under "
                    f"temp_images/; got {cached_image_path}. "
                    "Falling back to live image generation."
                )
            else:
                activity.logger.warning(
                    f"DEMO_RESEARCH_IMAGE_PATH does not exist: {cached_image_path}; "
                    "falling back to live image generation."
                )

        # Default styling options
        if styling_options is None:
            styling_options = ImageStylingOptions()

        # Generate image via OpenAI API. Pin the default so demo output does not
        # move with the ChatGPT image alias. Override with OPENAI_IMAGE_MODEL.
        image_model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2-2026-04-21")
        activity.logger.info(f"Generating image with model={image_model}")
        no_text_suffix = " Do not include any text, words, labels, numbers, or writing in the image."
        async with AsyncOpenAI() as client:
            result = await client.images.generate(
                model=image_model,
                prompt=prompt + no_text_suffix,
                quality=styling_options.quality,
                size=styling_options.size,
                output_format=styling_options.output_format,
                output_compression=styling_options.output_compression,
            )

        if not result.data:
            raise Exception("No image data returned from OpenAI API")

        # Extract base64 image data
        image_base64 = result.data[0].b64_json
        if image_base64 is None:
            raise Exception("No base64 image data returned from OpenAI API")
        image_bytes = base64.b64decode(image_base64)

        # Resize image for optimal PDF embedding
        if styling_options.resize_width:
            image: Image.Image = Image.open(BytesIO(image_bytes))

            # Calculate proportional height
            aspect_ratio = image.height / image.width
            new_height = int(styling_options.resize_width * aspect_ratio)

            # Resize with high-quality resampling
            image = image.resize(
                (styling_options.resize_width, new_height), Image.Resampling.LANCZOS
            )

            # Save to bytes
            output = BytesIO()
            format_map = {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}
            image.save(
                output,
                format=format_map.get(styling_options.output_format, "PNG"),
                optimize=True,
            )
            image_bytes = output.getvalue()

        # Determine MIME type
        mime_type = f"image/{styling_options.output_format}"

        # Save image to temp file
        import datetime
        from pathlib import Path

        temp_dir = Path("temp_images")
        temp_dir.mkdir(exist_ok=True)

        timestamp = datetime.datetime.now().strftime(
            "%Y%m%d_%H%M%S_%f"
        )  # Include microseconds for uniqueness
        ext = styling_options.output_format
        image_path = temp_dir / f"generated_image_{timestamp}.{ext}"

        # Write image bytes to file
        with open(image_path, "wb") as f:
            f.write(image_bytes)

        activity.logger.info(
            f"Successfully generated image: {len(image_bytes)} bytes, "
            f"type: {mime_type}, saved to: {image_path}"
        )

        return ImageGenerationResult(
            image_file_path=str(image_path), mime_type=mime_type, success=True
        )

    except Exception as e:
        activity.logger.error(f"Image generation failed: {str(e)}")

        # Check for non-retryable errors
        error_str = str(e)
        error_type = type(e).__name__
        non_retryable_indicators = [
            "403",  # Forbidden - organization not verified
            "invalid_request_error",  # Invalid request configuration
            "Your organization must be verified",  # Specific verification error
            "insufficient_quota",  # Quota exceeded
            "invalid_api_key",  # Auth errors
            "PydanticSerializationError",  # Serialization errors
            "invalid utf-8 sequence",  # UTF-8 encoding errors
        ]

        # If this is a non-retryable error, raise ApplicationError
        if any(indicator in error_str for indicator in non_retryable_indicators) or any(
            indicator in error_type for indicator in non_retryable_indicators
        ):
            from temporalio.exceptions import ApplicationError

            raise ApplicationError(
                f"Image generation failed with non-retryable error: {str(e)}",
                non_retryable=True,
                type="ImageGenerationError",
            )

        # Otherwise return graceful failure for retryable errors
        return ImageGenerationResult(
            image_file_path=None,
            mime_type="image/png",
            success=False,
            error_message=f"Image generation failed: {str(e)}",
        )
