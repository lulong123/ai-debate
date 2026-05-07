"""Perspective agent: defends a debate position."""


from app.agents.base import BaseAgent, load_prompt

# Cache the template at module level to avoid disk reads on every turn
_PROMPT_TEMPLATE = load_prompt("perspective.md")


class PerspectiveAgent(BaseAgent):
    def __init__(self, position_id: str, position_name: str, position_description: str):
        system_prompt = _PROMPT_TEMPLATE.format(
            position_name=position_name,
            position_description=position_description,
            context="",
        )
        super().__init__(system_prompt=system_prompt)
        self.position_id = position_id
        self.position_name = position_name
        self.position_description = position_description

    def _build_messages(self, context: str, user_message: str) -> list[dict]:
        system_prompt = _PROMPT_TEMPLATE.format(
            position_name=self.position_name,
            position_description=self.position_description,
            context=context,
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
