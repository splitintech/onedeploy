"""Stable, deliberately value-free public errors."""
EXIT_CODES = {"internal_error": 1,"invalid_config": 2, "unsupported": 3, "approval_required": 4,
              "authentication_failed": 5, "provider_failed": 6, "uncertain": 7,
              "cancelled": 8, "stale_plan": 9, "locked": 10}

class OnedeployError(Exception):
    def __init__(self, code, message, *, retryable=False, uncertain=False, mutation_possible=False):
        super().__init__(message)
        self.code, self.message = code, message
        self.retryable, self.uncertain, self.mutation_possible = retryable, uncertain, mutation_possible
        self.exit_code = EXIT_CODES.get(code, 6)

    def to_dict(self):
        return {"code": self.code, "message": self.message, "retryable": self.retryable,
                "uncertain": self.uncertain, "mutation_possible": self.mutation_possible}
