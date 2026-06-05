import yaml

from .screenplay import Screenplay


def screenplay_to_yaml(screenplay: Screenplay) -> str:
    return yaml.safe_dump(
        screenplay.model_dump(mode="json"),
        allow_unicode=True,
        sort_keys=False,
        width=100,
    )

