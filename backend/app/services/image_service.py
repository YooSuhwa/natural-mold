from __future__ import annotations

import base64
import logging
import re
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.agent import Agent

logger = logging.getLogger(__name__)

IMAGE_GEN_SYSTEM_PROMPT = """### [Constraint & Character Base (Refer to moldy_main.jpg)]
* **Base Character:** 'Moldy' from `moldy_main.jpg`. Maintain its exact translucent teal jelly \
texture, simple black bead eyes, smile, and head sprout.
* **Aesthetics:** High-end, premium 3D render. Minimalist. Ensure the character's squishy, \
light-transmissive jelly nature is preserved.
* **Prop Mapping Principle (Optimized for Small Icons):**
    * **Direct Interaction:** All items must be **directly worn** by Moldy or **held** in its \
jelly hands. (No floating elements).
    * **High Contrast:** Props must be made of **solid, opaque materials** (like matte plastic, \
metal, or wood) with bold colors to ensure high visibility even when the image is small.
* **Background:** **ALWAYS set to full transparency (Alpha Channel).**

### [Logic: Role to Visual Mapping]
Follow this extraction logic to create the visual identity:
1. **{Agent_Name}**: Identify the name of the agent.
2. **{Visual_Prop_Wearable}**: Map to a sophisticated 3D accessory (e.g., smart glasses, a hat, \
a lanyard/ID card). Material: Solid and opaque.
3. **{Visual_Prop_Holdable}**: Map to a single, high-quality 3D tool (e.g., a smartphone, a stack \
of folders, a glowing orb). Material: Bold, solid colors.
4. **{Pose}**: Adapt Moldy's hand pose to firmly hold the item or interact with the wearable.

### [Output Format: Image Generation Prompt]
Generate the final image using this template:

"A high-quality, premium 3D render of the 'Moldy' character (based on the reference image), \
maintaining its translucent teal jelly body and head sprout against a fully transparent \
(alpha channel) background. For its role as [{Agent_Name}], it is wearing distinctly-colored \
[{Visual_Prop_Wearable}] and directly holding [{Visual_Prop_Holdable}]. All props use opaque \
materials and bold, distinct colors to provide clear visual contrast against Moldy's translucent \
jelly body. Hand holds items firmly. Clean 8k render."
"""

_STATIC_DIR = Path(__file__).parent.parent.parent / "static"
_REFERENCE_IMAGE = _STATIC_DIR / "moldy_main.png"


def _load_reference_image_base64() -> str:
    """Load the Moldy reference image as base64."""
    return base64.b64encode(_REFERENCE_IMAGE.read_bytes()).decode()


def _extract_image_data(content: str | list) -> bytes:
    """Extract image bytes from OpenRouter/Gemini response content."""
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image_url":
                url = item["image_url"]["url"]
                if url.startswith("data:"):
                    # data:image/png;base64,<data>
                    b64 = url.split(",", 1)[1]
                    return base64.b64decode(b64)
            if isinstance(item, dict) and item.get("type") == "image":
                # Some models return {"type": "image", "data": "<base64>"}
                return base64.b64decode(item["data"])

    text = content if isinstance(content, str) else str(content)

    # Try to find base64 data
    b64_match = re.search(
        r"data:image/[a-z]+;base64,([A-Za-z0-9+/=]+)",
        text,
    )
    if b64_match:
        return base64.b64decode(b64_match.group(1))

    # Try raw base64 block (at least 100 chars)
    raw_match = re.search(r"([A-Za-z0-9+/]{100,}={0,2})", text)
    if raw_match:
        return base64.b64decode(raw_match.group(1))

    raise ValueError("Could not extract image data from response")


async def generate_agent_image(
    db: AsyncSession,
    agent: Agent,
    custom_prompt: str | None = None,
) -> str:
    """Generate a Moldy avatar for an agent and save it.

    Returns the API URL for the generated image.
    """
    if not settings.image_gen_api_key:
        raise ValueError("IMAGE_GEN_API_KEY is not configured")

    ref_b64 = _load_reference_image_base64()

    agent_desc = custom_prompt or (
        f"Agent Name: {agent.name}\n"
        f"Description: {agent.description or 'N/A'}\n"
        f"Role/System Prompt: {(agent.system_prompt or '')[:500]}"
    )

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.image_gen_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.image_gen_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.image_gen_model,
                "messages": [
                    {"role": "system", "content": IMAGE_GEN_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{ref_b64}",
                                },
                            },
                            {"type": "text", "text": agent_desc},
                        ],
                    },
                ],
            },
        )
        resp.raise_for_status()

    data = resp.json()
    content = data["choices"][0]["message"]["content"]

    image_bytes = _extract_image_data(content)

    # Save to disk
    save_dir = Path(settings.agent_image_dir) / str(agent.id)
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / "avatar.png"
    save_path.write_bytes(image_bytes)

    # Update DB
    agent.image_path = str(save_path)
    await db.commit()
    await db.refresh(agent)

    return f"/api/agents/{agent.id}/image"
