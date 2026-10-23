import base64
import datetime
import os
from pathlib import Path

from openai import APIStatusError, AsyncOpenAI
from temporalio import activity
from temporalio.exceptions import ApplicationError

IMAGE_DIR = Path("temp_images")

# Shorter than the activity's start-to-close timeout (orchestrator_agent.py),
# so an abandoned request cannot overlap its retry.
IMAGE_HTTP_TIMEOUT_SECONDS = 150

_NO_TEXT_SUFFIX = (
    " Do not include any text, words, labels, numbers, or writing in the image."
)


@activity.defn
async def generate_image(prompt: str) -> str:
    """Generate an image with OpenAI and return the saved file's path.

    Retries are Temporal's: the client makes a single request, and any failure
    other than a rejected request propagates so the activity retry policy
    applies.

    If DEMO_RESEARCH_IMAGE_PATH is set (validated at worker startup), that
    image is returned instead of generating a new one.
    """
    cached_image_path = os.getenv("DEMO_RESEARCH_IMAGE_PATH", "").strip()
    if cached_image_path:
        activity.logger.info(f"Using cached demo research image: {cached_image_path}")
        return cached_image_path

    # Pin the default so demo output does not move with the image alias.
    image_model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2-2026-04-21")
    activity.logger.info(f"Generating image with model={image_model}")
    try:
        async with AsyncOpenAI(
            max_retries=0, timeout=IMAGE_HTTP_TIMEOUT_SECONDS
        ) as client:
            result = await client.images.generate(
                model=image_model,
                prompt=prompt + _NO_TEXT_SUFFIX,
                quality="low",
                size="1024x1024",
                output_format="jpeg",
                output_compression=65,
            )
    except APIStatusError as e:
        # A rejected request (bad parameters, auth, unknown model, unverified
        # org) cannot succeed on retry. Same policy as the model activities:
        # 4xx is permanent except timeout, conflict and rate limit.
        if not 400 <= e.status_code < 500 or e.status_code in (408, 409, 429):
            raise
        raise ApplicationError(
            f"Image request rejected: {e}",
            non_retryable=True,
            type="ImageRequestRejected",
        ) from e

    if not result.data or result.data[0].b64_json is None:
        raise ApplicationError("No image data returned from OpenAI API")
    image_bytes = base64.b64decode(result.data[0].b64_json)

    IMAGE_DIR.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    image_path = IMAGE_DIR / f"generated_image_{timestamp}.jpeg"
    image_path.write_bytes(image_bytes)

    activity.logger.info(
        f"Generated image: {len(image_bytes)} bytes, saved to {image_path}"
    )
    return str(image_path)
