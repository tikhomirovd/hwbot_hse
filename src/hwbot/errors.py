from __future__ import annotations


class HwbotError(Exception):
    pass


class DeadlineClosedError(HwbotError):
    pass


class AlreadyBoundError(HwbotError):
    pass


class StudentTakenError(HwbotError):
    pass


class HomeworkNotFoundError(HwbotError):
    pass


class NotRegisteredError(HwbotError):
    pass
