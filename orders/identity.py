from dataclasses import dataclass


class OrderConflict(ValueError):
    pass


@dataclass(frozen=True)
class OrderIntent:
    order_id: str
    configuration_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ValueError('order ID must be a nonempty string')
        self.order_id.encode('utf-8')
        if not isinstance(self.configuration_id, str) or not self.configuration_id:
            raise ValueError('configuration ID must be a nonempty string')

    @property
    def correlation(self) -> str:
        return self.order_id


def same_submission(original: OrderIntent, received: OrderIntent) -> bool:
    return original == received
