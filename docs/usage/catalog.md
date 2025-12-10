# Catalog Management

## Crawl links

```
neodb-manage cat [--save] <url>  # parse / save a supported link
neodb-manage crawl <url>  # crawl all recognizable links from a page
```
## Data Management

- `neodb-manage catalog integrity`: Check and fix integrity issues for merged and deleted items
  - Use `--fix` to automatically fix issues
  - Example: `neodb-manage catalog integrity --fix`

- `neodb-manage catalog purge`: Purge deleted items from the database
  - Use `--fix` to actually perform the deletion
  - Example: `neodb-manage catalog purge --fix`

- `neodb-manage catalog migrate`: Run specified migration scripts
  - Requires `--name` to specify which migration to run
  - Example: `neodb-manage catalog migrate --name merge_works`
  - Available migrations:
    - `merge_works`: Merge work items (2025-03-01)
    - `fix_deleted_edition`: Fix soft deleted edition items (2025-02-08)
    - `fix_bangumi`: Fix Bangumi-related items (2025-04-20)

## Search

- `neodb-manage catalog search`: Search items in the index
  - Use `--query` to specify the search query
  - Example: `neodb-manage catalog search --query "Harry Potter"`

- `neodb-manage catalog extsearch`: Search external sites
  - Use `--query` for search terms and `--category` to filter by category
  - Example: `neodb-manage catalog extsearch --query "Inception" --category movie`

## Index Management

- `neodb-manage catalog idx-info`: Show information about the search index
  - Example: `neodb-manage catalog idx-info`

- `neodb-manage catalog idx-init`: Check and create the index if it doesn't exist
  - Example: `neodb-manage catalog idx-init`

- `neodb-manage catalog idx-destroy`: Delete the entire search index
  - Requires confirmation or `--yes` flag
  - Example: `neodb-manage catalog idx-destroy --yes`

- `neodb-manage catalog idx-alt`: Update index schema (currently not implemented)

- `neodb-manage catalog idx-delete`: Delete all documents in the index
  - Example: `neodb-manage catalog idx-delete`

- `neodb-manage catalog idx-reindex`: Rebuild the search index
  - Use `--batch-size` to specify how many items to process at once
  - Example: `neodb-manage catalog idx-reindex --batch-size 500`

- `neodb-manage catalog idx-get`: View one document in the index
  - Requires `--url` to specify which item to retrieve
  - Example: `neodb-manage catalog idx-get --url "https://example.com/item/123"`
