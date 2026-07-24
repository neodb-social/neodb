class ActivityPubError(BaseException):
    """
    A problem with an ActivityPub message
    """


class ActivityPubFormatError(ActivityPubError):
    """
    A problem with an ActivityPub message's format/keys
    """


class ActorMismatchError(ActivityPubError):
    """
    The actor is not authorised to do the action we saw
    """


class ActivityPubDeliveryError(ValueError):
    """
    A remote server refused (4xx) an activity we delivered.

    Subclasses ValueError as that is what delivery failures used to raise.
    """

    # Refusals that mean "not right now" rather than "never"
    retryable_statuses = [408, 429]
    # Refusals that mean the server will not take this activity from us at all
    unauthorized_statuses = [401, 403]

    def __init__(self, uri: str, status_code: int, content: bytes) -> None:
        self.uri = uri
        self.status_code = status_code
        self.content = content
        super().__init__(f"POST error to {uri}: {status_code} {content!r}")

    @property
    def retryable(self) -> bool:
        return self.status_code in self.retryable_statuses

    @property
    def unauthorized(self) -> bool:
        return self.status_code in self.unauthorized_statuses
