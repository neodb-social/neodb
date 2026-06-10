class ParseError(Exception):
    def __init__(self, scraper, field):
        msg = f'{type(scraper).__name__}: Error parsing field "{field}" for url {scraper.url}'
        super().__init__(msg)
