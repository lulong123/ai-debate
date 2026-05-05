"""Perspective agent: represents a discussion angle."""


from app.agents.base import BaseAgent, load_prompt

# Cache the template at module level to avoid disk reads on every turn
_PROMPT_TEMPLATE = load_prompt("perspective.md")


class PerspectiveAgent(BaseAgent):
    def __init__(self, angle_id: str, angle_name: str, angle_description: str):
        system_prompt = _PROMPT_TEMPLATE.format(
            angle_name=angle_name,
            angle_description=angle_description,
            context="",
        )
        super().__init__(system_prompt=system_prompt)
        self.angle_id = angle_id
        self.angle_name = angle_name
        self.angle_description = angle_description
        self.conceded = False

    def _build_messages(self, context: str, user_message: str) -> list[dict]:
        system_prompt = _PROMPT_TEMPLATE.format(
            angle_name=self.angle_name,
            angle_description=self.angle_description,
            context=context,
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

    def check_concede(self, response: str) -> bool:
        """Check if the agent conceded in its response."""
        return response.strip().startswith("[CONCEDE]")
