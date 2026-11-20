SUPPORTED_STATUSES = {"429", "500", "502", "503"}


def parse_simulation(header: str | None, key_count: int) -> dict[str, int]:
    key_ids = {f"key_{index}" for index in range(1, key_count + 1)}
    pairs: dict[str, int] = {}
    for pair in (header or "").split(","):
        key_id, _, status = pair.partition("=")
        key_id, status = key_id.strip(), status.strip()
        if (key_id == "*" or key_id in key_ids) and status in SUPPORTED_STATUSES:
            pairs[key_id] = int(status)

    wildcard = pairs.pop("*", None)
    if wildcard is not None:
        return {key_id: pairs.get(key_id, wildcard) for key_id in key_ids}
    return pairs


class SimulatedProviderError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
