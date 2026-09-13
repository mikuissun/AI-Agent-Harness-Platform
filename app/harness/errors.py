class HarnessError(Exception):
    """A stable, safe error code without adapter exception details."""


class DuplicateToolError(HarnessError):
    def __init__(self) -> None:
        super().__init__("duplicate_tool_name")


class UnknownToolError(HarnessError):
    def __init__(self) -> None:
        super().__init__("unknown_tool")
