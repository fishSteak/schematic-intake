"""Errors safe to surface in task state without HTTP bodies or credentials."""


class LLMError(RuntimeError):
    pass


class ConfigurationError(LLMError):
    pass


class LLMCancelled(LLMError):
    pass


class BudgetExceeded(LLMError):
    pass


class ProtocolError(LLMError):
    pass


class OutputValidationError(ProtocolError):
    pass


class ToolError(LLMError):
    pass


class LLMHTTPError(LLMError):
    def __init__(self, status_code: int, *, retryable: bool = False):
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(f"LLM service returned HTTP {status_code}")
