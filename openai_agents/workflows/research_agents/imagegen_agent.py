from datetime import timedelta

from agents import Agent
from pydantic import BaseModel
from temporalio.contrib import openai_agents as temporal_agents

from openai_agents.workflows.image_generation_activity import generate_image

IMAGE_GEN_PROMPT = (
    "You are an expert visual content specialist who creates compelling images "
    "for research reports. You will be provided with a research query that has been enriched "
    "with user preferences and context.\\n\\n"
    "Your responsibilities:\\n"
    "1. Analyze the research topic and identify key visual themes\\n"
    "2. Generate a 2-sentence image description that captures the essence of the research\\n"
    "3. Call the generate_image tool with your description to create the actual image\\n"
    "4. Return the results with the image file path and notes about the visual concept\\n\\n"
    "Guidelines for image descriptions:\\n"
    "- Focus on thematic, atmospheric visuals that evoke the mood and subject of the research\\n"
    "- NEVER include any text, words, labels, numbers, charts, graphs, or data visualizations\\n"
    "- NEVER include signs, banners, headlines, captions, or any written content\\n"
    "- Prefer symbolic, abstract, or scenic imagery — think editorial photography or concept art\\n"
    "- Consider the research domain and choose evocative visual metaphors\\n"
    "- Make descriptions specific and detailed for high-quality output\\n"
    "- Always end your description with: 'The image contains no text, numbers, or labels.'\\n\\n"
    "Examples:\\n"
    "- Research query: 'Sustainable energy solutions for small businesses'\\n"
    "  Image description: 'An aerial view of a sunlit commercial district with solar panels "
    "gleaming on rooftops and wind turbines spinning on green hills in the distance, rendered "
    "in a warm, optimistic illustration style. The image contains no text, numbers, or labels.'\\n\\n"
    "- Research query: 'Impact of artificial intelligence on healthcare diagnostics'\\n"
    "  Image description: 'A close-up of a glowing neural network pattern overlaid on a softly "
    "lit hospital corridor, with gentle blue and white light suggesting advanced technology "
    "woven into a calm medical environment. The image contains no text, numbers, or labels.'\\n\\n"
    "IMPORTANT: After calling generate_image tool:\\n"
    "- Set success to true if the tool returns success=true\\n"
    "- Include the image_file_path from the tool response in your output\\n"
    "- If the tool fails, set success to false and include the error message"
)


class ImageGenData(BaseModel):
    """Output from image generation agent"""

    success: bool
    """Whether image generation was successful"""

    image_description: str
    """The 2-sentence description used for generating the image"""

    image_file_path: str | None = None
    """Path to the generated image file (if successful)"""

    notes: str
    """Notes about the visual concept and design choices"""

    error_message: str | None = None
    """Error message if image generation failed"""


def new_imagegen_agent() -> Agent:
    """Create a new image generation agent."""
    return Agent(
        name="ImageGenAgent",
        instructions=IMAGE_GEN_PROMPT,
        model="gpt-4o-mini",  # Fast, cost-effective for description generation
        tools=[
            temporal_agents.workflow.activity_as_tool(
                generate_image, start_to_close_timeout=timedelta(seconds=60)
            )
        ],
        output_type=ImageGenData,
    )
