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
    A remote server permanently refused (4xx) an activity we delivered, so
    sending the same activity again will not succeed.

    Subclasses ValueError as that is what delivery failures used to raise.
    """

    def __init__(self, uri: str, status_code: int, content: bytes) -> None:
        self.uri = uri
        self.status_code = status_code
        self.content = content
        super().__init__(f"POST error to {uri}: {status_code} {content!r}")
