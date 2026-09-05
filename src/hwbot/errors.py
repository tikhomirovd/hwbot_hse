from __future__ import annotations


class HwbotError(Exception):
    pass


class DeadlineClosedError(HwbotError):
    pass


class NotIssuedError(HwbotError):
    def __init__(self, issued_at: int | None = None) -> None:
        super().__init__("Работа ещё не выдана")
        self.issued_at = issued_at


class AlreadyBoundError(HwbotError):
    pass


class StudentTakenError(HwbotError):
    pass


class HomeworkNotFoundError(HwbotError):
    pass


class NotRegisteredError(HwbotError):
    pass


class CourseError(HwbotError):
    pass
