import enum


class InputSource(str, enum.Enum):
    path = "path"
    query = "query"
    query_list = "query_list"
    body = "body"
    body_direct = "body_direct"
    query_and_body_direct = "query_and_body_direct"
    file = "file"
