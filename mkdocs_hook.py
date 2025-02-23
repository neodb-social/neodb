import json


def on_page_markdown(markdown, page, config, **kwargs):
    if page.url == "servers/":
        with open(config.docs_dir + "/servers.json") as f:
            servers = json.load(f)
            m = ""
            for s in servers["servers"]:
                host = s["host"]
                name = s.get("name", host)
                admin = s.get("admin", [])
                label = s.get("label", [])
                language = s.get("language", [])
                description = s.get("description", "")
                m += f" - **[{name}](https://{host})**"
                if label:
                    m += f" {' '.join([f'`{a}`' for a in label])}"
                if language:
                    m += f" {' '.join([f'`{a}`' for a in language])}"
                if description:
                    m += f"  \n  {description}"
                if admin:
                    m += f"  \n  admin: {', '.join([f'`{a}`' for a in admin])}"
                m += "\n"
            return markdown.replace("{servers}", m)
