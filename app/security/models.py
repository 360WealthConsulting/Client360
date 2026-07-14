from dataclasses import dataclass

@dataclass(frozen=True)
class Principal:
    user_id: int
    email: str
    display_name: str
    capabilities: frozenset[str]

    def can(self, capability: str) -> bool:
        return capability in self.capabilities
