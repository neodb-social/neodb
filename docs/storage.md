# Storage

NeoDB keeps the files which users upload, for example item covers, avatars and post attachments, in the backend which `MEDIA_BACKEND` selects in `.env`. The default is `local://`, which writes to the `neodb-media` and `takahe-media` directories below `NEODB_DATA`. The other option is S3, or one of the S3-compatible servers below, which you can run together with your instance.

To test storage configuration, you can use the following command to upload a test file and check if it's accessible:

```
neodb-manage catalog storage-test
```

`MEDIA_URL` says where those files are served from. If its host is your site domain or one of `NEODB_ALTERNATIVE_DOMAINS`, NeoDB renders media as path-only URLs such as `/m/item/x.jpg`, so a page served on an alias domain loads its media from that same domain. Make sure every alias routes that path to the same storage. A separate media host, for example a CDN, keeps absolute URLs.

`MEDIA_URL` must have a path of its own. On S3, a bare `https://your.site.domain/` puts every media key in the URL space of the site itself, where it collides with NeoDB's own paths, and `neodb-manage check` reports it as `neodb.E005`.

## Minio

If you are using Minio or [its forks](https://github.com/minio/minio/network) for local S3-compatible storage, add the following configuration to `compose.override.yml` (change `minio/minio` to your chosen fork as the original one is unmaintained and may have known security issues):

```
services:
  minio:
    image: minio/minio:latest
    command: server --console-address :9001
    environment:
      MINIO_DOMAIN: ${MINIO_DOMAIN}
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: change_password
      MINIO_VOLUMES: /var/lib/minio
    volumes:
      - ${NEODB_DATA:-../data}/minio-files:/var/lib/minio
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
    ports:
      - 9000:9000
      - 9001:9001
```

And add these settings to `.env`:
```
MINIO_DOMAIN=my.media.domain
MEDIA_BACKEND=s3-insecure://minioadmin:change_password@minio:9000/media
MEDIA_URL=https://my.media.domain/media/
```

Also make sure `my.media.domain` maps to your Minio server (port 9000 as configured above).


## Garage

[Garage](https://garagehq.deuxfleurs.fr/) is a lightweight S3-compatible storage engine. Version 2.3.0 and later can configure a single-node cluster and its first bucket at start, thus the commands below are much fewer than the [Garage quick start](https://garagehq.deuxfleurs.fr/documentation/quick-start/) gives for a cluster.

Create a `garage.toml` configuration file. Make a new `rpc_secret` with `openssl rand -hex 32`:
```
metadata_dir = "/var/lib/garage/meta"
data_dir = "/var/lib/garage/data"
replication_factor = 1
rpc_bind_addr = "[::]:3901"
rpc_secret = "YOUR_RPC_SECRET"

[s3_api]
s3_region = "garage"
api_bind_addr = "[::]:3900"

[s3_web]
bind_addr = "[::]:3902"
root_domain = ".my.media.domain"
```

Add the following to `compose.override.yml`. Make the access key with `echo GK$(openssl rand -hex 16)` and the secret key with `openssl rand -hex 32`:
```
services:
  garage:
    image: dxflrs/garage:v2.4.1
    command: /garage server --single-node --default-bucket
    environment:
      GARAGE_DEFAULT_ACCESS_KEY: YOUR_ACCESS_KEY
      GARAGE_DEFAULT_SECRET_KEY: YOUR_SECRET_KEY
      GARAGE_DEFAULT_BUCKET: media
    volumes:
      - ${NEODB_DATA:-../data}/garage/garage.toml:/etc/garage.toml
      - ${NEODB_DATA:-../data}/garage/data:/var/lib/garage/data
      - ${NEODB_DATA:-../data}/garage/meta:/var/lib/garage/meta
    ports:
      - 3900:3900
      - 3902:3902
```

`--single-node` makes the cluster layout, and `--default-bucket` makes the key and the `media` bucket. Neither of them makes the bucket public, thus give the bucket public read access after the first start:
```
docker compose exec garage /garage -c /etc/garage.toml bucket website --allow media
```

Add these settings to `.env`, using the same key ID and secret key:
```
MEDIA_BACKEND=s3-insecure://YOUR_ACCESS_KEY:YOUR_SECRET_KEY@garage:3900/media
MEDIA_URL=https://media.my.media.domain/
```

Garage serves files publicly via its S3 Web endpoint (port 3902) using virtual-host-style routing. The `MEDIA_URL` hostname must match `{bucket}.{root_domain}` configured in the `[s3_web]` section of `garage.toml`. For example, with `root_domain = ".my.media.domain"` and bucket `media`, the public URL becomes `https://media.my.media.domain/`. Make sure DNS for that hostname points to Garage's port 3902.


## SeaweedFS

[SeaweedFS](https://github.com/seaweedfs/seaweedfs) is a distributed storage system with S3 API support. Add the following to `compose.override.yml`, mounting an [S3 credentials config](https://github.com/seaweedfs/seaweedfs/wiki/Amazon-S3-API) file with anonymous `Read` and an admin identity (see [Docker Compose for S3](https://github.com/seaweedfs/seaweedfs/wiki/Docker-Compose-for-S3) for details):

```
services:
  seaweedfs:
    image: chrislusf/seaweedfs
    command: "server -s3 -s3.config /etc/seaweedfs/config.json"
    volumes:
      - ${NEODB_DATA:-../data}/seaweedfs/config.json:/etc/seaweedfs/config.json
      - ${NEODB_DATA:-../data}/seaweedfs/data:/data
    ports:
      - 8333:8333
```

Create the `media` bucket after first start (using [awscli](https://aws.amazon.com/cli/) or any S3 client):
```
aws --endpoint-url http://localhost:8333 s3 mb s3://media
```

Add these settings to `.env`, matching the credentials in the config file:
```
MEDIA_BACKEND=s3-insecure://some_access_key:some_secret_key@seaweedfs:8333/media
MEDIA_URL=https://my.media.domain/media/
```

Make sure `my.media.domain` maps to your SeaweedFS server (port 8333). Files are publicly readable via the same port thanks to the anonymous read identity.


## VersityGW

[VersityGW](https://github.com/versity/versitygw) is an S3 gateway that keeps each object as a plain file in a local directory. Add the following to `compose.override.yml`:

```
services:
  versitygw:
    image: ghcr.io/versity/versitygw:v1.8.0
    environment:
      ROOT_ACCESS_KEY: neodbadmin
      ROOT_SECRET_KEY: change_password
      VGW_BACKEND: posix
      VGW_BACKEND_ARGS: /data/s3
      VGW_IAM_DIR: /data/iam
    volumes:
      - ${NEODB_DATA:-../data}/versitygw/s3:/data/s3
      - ${NEODB_DATA:-../data}/versitygw/iam:/data/iam
    ports:
      - 7070:7070
```

Put the `s3` directory on a filesystem that supports extended attributes. VersityGW keeps the content type, the ETag and the bucket policy in extended attributes. If your filesystem does not support them, add `--sidecar /data/meta` to `VGW_BACKEND_ARGS` and mount a second directory at `/data/meta`, where VersityGW will keep the same data as plain files. Select one of the two modes before you create the bucket, because VersityGW does not read the metadata of the other mode.

Do not move an existing media folder into the bucket directory. VersityGW gives the files no content type, thus it sends them as `text/plain` and browsers will not show the images. Copy the folder in through the S3 API instead, which sets the content type from the file extension:
```
aws --endpoint-url http://localhost:7070 s3 sync /path/to/neodb-media s3://media/
```

If the folder is too large to copy twice, you can put it in the bucket directory and then give each object a content type with a server-side copy, which does not send the data again:
```
aws --endpoint-url http://localhost:7070 s3api copy-object --bucket media \
  --key covers/example.jpg --copy-source media/covers/example.jpg \
  --metadata-directive REPLACE --content-type image/jpeg
```
Such objects still have no ETag. Add `--default-etag <value>` to `VGW_BACKEND_ARGS` if your clients need one. VersityGW has no command to build the metadata of existing files ([feature request](https://github.com/versity/versitygw/issues/2304)).

Create the `media` bucket after first start, then let anonymous users read it. VersityGW does not allow bucket ACLs by default, so you must add a bucket policy (using [awscli](https://aws.amazon.com/cli/) or any S3 client):
```
export AWS_ACCESS_KEY_ID=neodbadmin
export AWS_SECRET_ACCESS_KEY=change_password
export AWS_DEFAULT_REGION=us-east-1
aws --endpoint-url http://localhost:7070 s3 mb s3://media
aws --endpoint-url http://localhost:7070 s3api put-bucket-policy --bucket media \
  --policy '{"Statement":[{"Effect":"Allow","Principal":"*","Action":"s3:GetObject","Resource":"arn:aws:s3:::media/*"}]}'
```

Add these settings to `.env`:
```
MEDIA_BACKEND=s3-insecure://neodbadmin:change_password@versitygw:7070/media
MEDIA_URL=https://my.media.domain/media/
```

Make sure `my.media.domain` maps to your VersityGW server (port 7070 as configured above). NeoDB always builds media URLs with `https`, so serve that domain through a TLS reverse proxy in front of VersityGW.


## S2

[S2](https://github.com/mojatter/s2) is a small S3 server that can serve a directory of files it did not write itself. It finds the content type of each file from the file extension, so you can put an existing media folder into a bucket and use it immediately. S2 is a young project, and its authors give local development as the primary use.

Write a configuration file, for example `${NEODB_DATA:-../data}/s2/s2.json`. The `"*"` account is the anonymous reader, which makes the media files publicly readable:
```
{
  "listen": ":9000",
  "type": "osfs",
  "root": "/var/lib/s2",
  "user": "neodbadmin",
  "password": "change_password",
  "users": [
    {
      "access_key_id": "*",
      "policy": {
        "Version": "2012-10-17",
        "Statement": [
          {
            "Sid": "PublicRead",
            "Effect": "Allow",
            "Action": "s3:GetObject",
            "Resource": "arn:aws:s3:::media/*"
          }
        ]
      }
    }
  ]
}
```

Add the following to `compose.override.yml`. Each directory below the root is a bucket, so the `media` bucket needs no separate creation step:
```
services:
  s2:
    image: mojatter/s2-server:0.17.0
    environment:
      S2_SERVER_CONFIG: /etc/s2/s2.json
      S2_SERVER_CONSOLE_LISTEN: ""
    volumes:
      - ${NEODB_DATA:-../data}/s2/s2.json:/etc/s2/s2.json
      - ${NEODB_DATA:-../data}/s2/data:/var/lib/s2
    ports:
      - 9000:9000
```

Add these settings to `.env`:
```
MEDIA_BACKEND=s3-insecure://neodbadmin:change_password@s2:9000/media
MEDIA_URL=https://my.media.domain/media/
```

Make sure `my.media.domain` maps to your S2 server (port 9000 as configured above). NeoDB always builds media URLs with `https`, so serve that domain through a TLS reverse proxy in front of S2.

To change from `MEDIA_BACKEND=local://`, merge the two local media directories into the bucket directory. With `local://`, NeoDB writes to `neodb-media` and takahe writes to `takahe-media`. With `s3://`, both write to the root of the same bucket, because the object key is the path below each local root. Move the contents of both directories into `s2/data/media`, with no renaming:
```
s2/data/media/
├── item/                     ┐
├── user/                     │
├── upload/                   │ from neodb-media
├── sync/                     │
├── export/                   ┘
├── attachments/              ┐
├── attachment_thumbnails/    │
├── profile_images/           │ from takahe-media
├── background_images/        │
├── emoji/                    │
├── config/                   ┘
└── .meta/                      written by S2
```
The two applications use different names at this level, thus their files do not conflict. S2 keeps in `.meta` the metadata of each file it receives through the S3 API. The files you move have no entry there, and S2 finds their content type from the file extension.

S2 gives the files it did not write a placeholder ETag. It also does not answer conditional requests, thus a browser gets the full file each time instead of a `304`. The example above turns the web console off; remove `S2_SERVER_CONSOLE_LISTEN` to get it on port 9001.
