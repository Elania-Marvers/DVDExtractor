from __future__ import annotations

from abc import ABC, abstractmethod


class ExtractionPlanStrategy(ABC):
    """Template de base pour une stratégie de planification d'extraction."""

    name = "base"
    priority = 100

    def __init__(self, profile) -> None:
        self.profile = profile

    @abstractmethod
    def supports(self) -> bool:
        """Retourne True si la stratégie est activable dans le contexte courant."""

    @abstractmethod
    def build(self, *args, **kwargs) -> list[dict]:
        """Construit une liste d'essais de transcodage.

        Doit retourner une liste de dictionnaires de commande compatible avec le
        dispatcher d'exécution.
        """
        raise NotImplementedError


class FfmpegProfileSpec:
    """Paramètres de base pour les appels ffmpeg utilisés par le planificateur."""

    @staticmethod
    def base_argv(ffmpeg: str | None) -> list[str]:
        if not ffmpeg:
            return []
        return [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-analyzeduration",
            "60M",
            "-probesize",
            "60M",
        ]
