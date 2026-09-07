from .article import *  # noqa
from .attachment import *  # noqa
from .collection import *  # noqa
from .note import *  # noqa
from .review import *  # noqa
from .shelf import *  # noqa
from .tag import *  # noqa
from .post import *  # noqa

from journal.models import Article, Collection, Note, Piece, Review, ShelfMember

from .article import ArticleSchema
from .collection import CollectionSchema
from .note import NoteSchema
from .review import ReviewSchema
from .shelf import MarkSchema

Piece.webhook_schemas.update(
    {
        ShelfMember: MarkSchema,
        Note: NoteSchema,
        Review: ReviewSchema,
        Collection: CollectionSchema,
        Article: ArticleSchema,
    }
)
