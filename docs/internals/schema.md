# Catalog wire schema

Catalog detail APIs and catalog URLs requested with `Accept: application/activity+json` serialize the same schemas. Generic API lists (trending and recommendations) use only the common item fields; typed detail and search responses add the fields below.

Notation: `T?` is nullable, `T[]` is a list, and **deprecated** fields are compatibility aliases. Dates are partial ISO dates (`YYYY`, `YYYY-MM`, or `YYYY-MM-DD`) unless noted otherwise.

## Common item fields

Emitted for `Edition`, `Movie`, `TVShow`, `TVSeason`, `TVEpisode`, `Album`, `Game`, `Podcast`, `PodcastEpisode`, `Performance`, `PerformanceProduction`, and `Work`:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Absolute canonical item URL |
| `type` | string | Model/AP type, such as `Edition` or `Movie` |
| `uuid` | string | Base62 item UUID |
| `url` | string | Relative web URL |
| `api_url` | string | Relative API URL |
| `category` | string | `book`, `movie`, `tv`, `music`, `game`, `podcast`, or `performance` |
| `parent_uuid` | string? | Parent Work, show, season, podcast, or performance UUID |
| `title` | string | Locale-selected display title |
| `description` | string | Locale-selected description |
| `localized_title` | `LocalizedLabel[]` | Titles by locale |
| `localized_description` | `LocalizedLabel[]` | Descriptions by locale |
| `cover_image_url` | string? | Absolute cover URL |
| `external_resources` | `ExternalResource[]?` | Known external source URLs |
| `credits` | `Credit[]` | Structured credits |
| `rating` | number? | Average grade from 1 to 10 |
| `rating_count` | integer? | Number of ratings |
| `rating_distribution` | `integer[]?` | Percentages for grades 1-2, 3-4, 5-6, 7-8, and 9-10 |
| `tags` | `string[]?` | Public tags; normally populated only on detail and search surfaces |
| `display_title` **deprecated** | string | Alias of `title` |
| `brief` **deprecated** | string | Alias of `description` |

### LocalizedLabel

| Field | Type |
| --- | --- |
| `lang` | string |
| `text` | string |

### Credit

| Field | Type |
| --- | --- |
| `role` | string |
| `name` | string |
| `character_name` | string |
| `person_url` | string? |

### ExternalResource

| Field | Type |
| --- | --- |
| `url` | string |

## Fields by type

Each type below includes the common fields unless stated otherwise.

### Work (`category: book`)

| Field | Type |
| --- | --- |
| — | No type-specific fields; no public typed API endpoint |

### Edition (`category: book`)

| Field | Type |
| --- | --- |
| `subtitle` | string? |
| `orig_title` | string? |
| `author` | string[] |
| `translator` | string[] |
| `language` | string[] |
| `publisher` | string[] |
| `pub_house` **deprecated** | string? |
| `pub_year` | integer? |
| `pub_month` | integer? |
| `format` | string? |
| `binding` **deprecated** | string? |
| `price` | string? |
| `pages` | integer or string? |
| `series` | string? |
| `imprint` | string? |
| `contents` | string? |
| `isbn` | string? |

### Movie (`category: movie`)

| Field | Type |
| --- | --- |
| `orig_title` | string? |
| `director` | string[] |
| `playwright` | string[] |
| `actor` | string[] |
| `producer` | string[] |
| `genre` | string[] |
| `language` | string[] |
| `origin_country` | string[] |
| `release_date` | string? |
| `official_site` | string? |
| `length` | integer? (seconds) |
| `imdb` | string? |
| `year` **deprecated** | integer? |
| `site` **deprecated** | string? |
| `duration` **deprecated** | string? |
| `area` **deprecated** | string[] |
| `showtime` **deprecated** | `Showtime[]` |

#### Showtime

| Field | Type |
| --- | --- |
| `time` | string |
| `region` | string |

### TVShow (`category: tv`)

| Field | Type |
| --- | --- |
| Movie fields above | including compatibility aliases |
| `season_count` | integer? |
| `episode_count` | integer? |
| `season_uuids` | string[] |

### TVSeason (`category: tv`)

| Field | Type |
| --- | --- |
| Movie fields above | including compatibility aliases |
| `season_number` | integer? |
| `episode_count` | integer? |
| `episode_uuids` | string[] |

### TVEpisode (`category: tv`)

| Field | Type |
| --- | --- |
| `episode_number` | integer? |

### Album (`category: music`)

| Field | Type |
| --- | --- |
| `genre` | string[] |
| `artist` | string[] |
| `company` | string[] |
| `length` | integer? (seconds) |
| `release_date` | string? |
| `album_type` | string[] |
| `media_format` | string[] |
| `track_list` | string? |
| `barcode` | string? |
| `duration` **deprecated** | integer? (milliseconds) |
| `media` **deprecated** | string? |

### Game (`category: game`)

| Field | Type |
| --- | --- |
| `genre` | string[] |
| `developer` | string[] |
| `publisher` | string[] |
| `platform` | string[] |
| `release_type` | string? |
| `release_date` | string? |
| `official_site` | string? |
| `release_year` **deprecated** | integer? |

### Podcast (`category: podcast`)

| Field | Type |
| --- | --- |
| `genre` | string[] |
| `host` | string[] |
| `language` | string[] |
| `official_site` | string? |
| `hosts` **deprecated** | string[] (alias of `host`) |

### PodcastEpisode (`category: podcast`)

| Field | Type |
| --- | --- |
| `guid` | string? |
| `pub_date` | datetime? (ISO 8601) |
| `media_url` | string? |
| `link` | string? |
| `length` | integer? (seconds) |
| `duration` **deprecated** | integer? (seconds) |

### Performance (`category: performance`)

| Field | Type |
| --- | --- |
| `orig_title` | string? |
| `genre` | string[] |
| `language` | string[] |
| `opening_date` | string? |
| `closing_date` | string? |
| `director` | string[] |
| `playwright` | string[] |
| `orig_creator` | string[] |
| `composer` | string[] |
| `choreographer` | string[] |
| `performer` | string[] |
| `actor` | `CrewMember[]` |
| `crew` | `CrewMember[]` |
| `official_site` | string? |

#### CrewMember

| Field | Type |
| --- | --- |
| `name` | string |
| `role` | string? |

### PerformanceProduction (`category: performance`)

| Field | Type |
| --- | --- |
| `orig_title` | string? |
| `language` | string[] |
| `opening_date` | string? |
| `closing_date` | string? |
| `director` | string[] |
| `playwright` | string[] |
| `orig_creator` | string[] |
| `composer` | string[] |
| `choreographer` | string[] |
| `performer` | string[] |
| `actor` | `CrewMember[]` |
| `crew` | `CrewMember[]` |
| `official_site` | string? |

### People

People detail responses (`GET /api/people/{uuid}`) and People returned by catalog search use a separate schema and do **not** include the common item fields.

| Field | Type |
| --- | --- |
| `id` | string |
| `uuid` | string |
| `url` | string |
| `api_url` | string |
| `people_type` | `person` or `organization` |
| `name` | string |
| `bio` | string |
| `localized_name` | `LocalizedLabel[]` |
| `localized_bio` | `LocalizedLabel[]` |
| `cover_image_url` | string? |
| `birth_date` | string? |
| `death_date` | string? |
| `official_site` | string? |
| `imdb` | string? |
| `display_name` **deprecated** | string (alias of `name`) |

## People and credit endpoints

| Method and path | Response | Notes |
| --- | --- | --- |
| `GET /api/people/{uuid}` | `People` | Individual person or organization |
| `GET /api/catalog/{item_type}/{uuid}/credit/?page=N` | `Page<CreditDetail>` | All credits; `item_type` accepts `item` or a concrete catalog type |
| `GET /api/people/{uuid}/work/?page=N` | `Page<PeopleWork>` | Unique credited items; no role or category filter |

### CreditItemType

| Value | Catalog model |
| --- | --- |
| `item` | Any `Item` |
| `book` | `Edition` (canonical) |
| `edition` | `Edition` |
| `movie` | `Movie` |
| `tv` | `TVShow` (canonical) |
| `tvshow` | `TVShow` |
| `tvseason` | `TVSeason` |
| `tvepisode` | `TVEpisode` |
| `album` | `Album` |
| `game` | `Game` |
| `podcast` | `Podcast` |
| `podcastepisode` | `PodcastEpisode` |
| `performance` | `Performance` |
| `performanceproduction` | `PerformanceProduction` |
| `work` | `Work` |

Name-only credits are included by the item credit endpoint with `person: null`.

### Page<T>

| Field | Type |
| --- | --- |
| `data` | `T[]` |
| `pages` | integer |
| `count` | integer |

### CreditDetail

| Field | Type |
| --- | --- |
| `role` | string |
| `name` | string |
| `character_name` | string |
| `person` | `PeopleSummary?` |

### PeopleSummary

| Field | Type |
| --- | --- |
| `id` | string |
| `uuid` | string |
| `url` | string |
| `api_url` | string |
| `people_type` | `person` or `organization` |
| `display_name` | string |
| `cover_image_url` | string? |

### PeopleWork

| Field | Type |
| --- | --- |
| `item` | `ItemSummary` |
| `credits` | `PeopleWorkCredit[]` |

### PeopleWorkCredit

| Field | Type |
| --- | --- |
| `role` | string |
| `name` | string |
| `character_name` | string |

### ItemSummary

| Field | Type |
| --- | --- |
| `id` | string |
| `type` | string |
| `uuid` | string |
| `url` | string |
| `api_url` | string |
| `category` | string |
| `parent_uuid` | string? |
| `display_title` | string |
| `cover_image_url` | string? |
