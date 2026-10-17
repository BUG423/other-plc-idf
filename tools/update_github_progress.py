"""汇总 GitHub 任务元数据；不执行开发任务、测试、PLC 操作或发布。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


START = "<!-- plcidf-progress:start -->"
END = "<!-- plcidf-progress:end -->"


def render_tasks(issues: list[dict], timestamp: str) -> str:
    """Issue 关闭是任务状态，不据此推断测试、硬件验收或工具链就绪。"""
    tasks = sorted((i for i in issues if "pull_request" not in i),
                   key=lambda i: i["number"])
    closed = sum(i["state"] == "closed" for i in tasks)
    lines = [f"更新时间（UTC）：{timestamp}", "",
             f"任务：{len(tasks)}；已关闭：{closed}；未关闭：{len(tasks) - closed}。",
             "仅汇总 GitHub Issue 状态；通过的测试与验收证据须查看任务正文。", "",
             "| 任务 | 状态 | 优先级 |", "|---|---|---|"]
    for issue in tasks:
        title = " ".join(issue["title"].split()).replace("|", "／")
        title = title.replace("[", "（").replace("]", "）").replace("<", "＜").replace(">", "＞")
        labels = {label["name"] for label in issue.get("labels", [])}
        priority = ", ".join(sorted(x for x in labels if x.startswith("priority:"))) or "—"
        state = ("已关闭" if issue["state"] == "closed" else
                 "已交付，待合并" if "status:review" in labels else "待完成")
        lines.append(f"| [#{issue['number']} {title}]({issue['html_url']}) | {state} | {priority} |")
    return "\n".join(lines)


def render_report(issues: list[dict], timestamp: str) -> str:
    # 独立追加报告评论，不读改写人工正文，避免与人工并发编辑发生丢失更新。
    return START + "\n" + render_tasks(issues, timestamp) + "\n" + END


def main() -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    event = json.loads(Path(event_path).read_text(encoding="utf-8")) if event_path else {}
    issue_event = event.get("issue")
    if issue_event:
        labels = {label["name"] for label in issue_event.get("labels", [])}
        removed_label = event.get("label", {}).get("name")
        if "plcidf-task" not in labels and removed_label != "plcidf-task":
            print("Non-task issue event skipped; no report posted.")
            return

    def api(path: str, method: str = "GET", data: dict | None = None):
        payload = json.dumps(data).encode() if data is not None else None
        request = Request(f"https://api.github.com/repos/{repo}/{path}", data=payload,
                          method=method, headers={"Authorization": f"Bearer {token}",
                          "Accept": "application/vnd.github+json",
                          "X-GitHub-Api-Version": "2022-11-28",
                          "Content-Type": "application/json"})
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    def list_issues(label: str) -> list[dict]:
        result = []
        page = 1
        while True:
            query = urlencode({"labels": label, "state": "all", "per_page": 100, "page": page})
            batch = api("issues?" + query)
            result.extend(i for i in batch if "pull_request" not in i)
            if len(batch) < 100:
                return result
            page += 1

    trackers = [i for i in list_issues("plcidf-tracking") if i["state"] == "open"]
    if len(trackers) != 1:
        raise ValueError("必须且只能有一个开放的 plcidf-tracking Issue")
    tracker = trackers[0]
    if event.get("issue", {}).get("number") == tracker["number"]:
        print("Tracking issue event skipped to prevent report loops.")
        return
    tasks = list_issues("plcidf-task")
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    body = render_report(tasks, timestamp)
    api(f"issues/{tracker['number']}/comments", method="POST", data={"body": body})
    print(f"Posted report to tracking issue #{tracker['number']}; no engineering tasks executed.")


if __name__ == "__main__":
    main()
