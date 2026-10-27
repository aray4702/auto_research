"""Tests for the tracker tool, against an in-memory GitHub."""

import unittest

from system.tools import tracker

CONFIG = tracker.load_config()
CALLER = ("builder", "run-1")


class FakeRepo:
    """Just enough of GitHub's labels and issues API to run setup and check twice."""

    repo = "o/r"

    def __init__(self, labels=(), issues=()):
        self.labels = {label["name"]: dict(label) for label in labels}
        self.issues = list(issues)
        self.writes = []

    def api(self, method, path, body=None, paginate=False):
        if method != "GET":
            self.writes.append((method, path, body))
        if method == "GET" and path.startswith("repos/o/r/labels"):
            return list(self.labels.values())
        if method == "POST" and path == "repos/o/r/labels":
            self.labels[body["name"]] = dict(body)
            return body
        if method == "PATCH" and path.startswith("repos/o/r/labels/"):
            name = tracker.urllib.parse.unquote(path.rsplit("/", 1)[1])
            self.labels[name].update(body)
            return self.labels[name]
        if method == "GET" and path.startswith("repos/o/r/issues?"):
            self.assertNoLabelFilter(path)
            return list(self.issues)
        if method == "POST" and path == "repos/o/r/issues":
            labels = [{"name": n} for n in body["labels"]]
            issue = {"number": len(self.issues) + 1, "labels": labels, "body": body["body"], "html_url": "url"}
            self.issues.append(issue)
            return issue
        raise AssertionError(f"unexpected call {method} {path}")

    @staticmethod
    def assertNoLabelFilter(path):
        # GitHub's ?labels= filter lags behind new issues; records must be found without it.
        if "labels=" in path:
            raise AssertionError(f"records must not be listed with a label filter: {path}")


class RulesSyncTest(unittest.TestCase):
    def test_rules_and_config_name_the_same_labels(self):
        in_rules = tracker.labels_in_rules(tracker.RULES.read_text())
        self.assertEqual(sorted(in_rules), sorted(label["name"] for label in CONFIG["labels"]))
        self.assertEqual(len(in_rules), len(set(in_rules)))

    def test_ellipsis_repeats_the_prefix(self):
        text = "## Items and labels\n\n| `a:x`, `…:y` | m |\n| `b:z` | m |\n\n## Next\n| `c:w` | m |\n"
        self.assertEqual(tracker.labels_in_rules(text), ["a:x", "a:y", "b:z"])

    def test_every_record_label_is_defined(self):
        names = {label["name"] for label in CONFIG["labels"]}
        for record in CONFIG["records"]:
            self.assertIn(record["label"], names)


class SetupTest(unittest.TestCase):
    def test_setup_creates_everything_then_has_nothing_to_do(self):
        repo = FakeRepo(labels=[{"name": "bug", "color": "d73a4a", "description": "Something isn't working"}])
        self.assertTrue(tracker.problems(repo, CONFIG))
        done = tracker.setup(repo, CONFIG, CALLER)
        self.assertEqual(sum(d.startswith("created label") for d in done), len(CONFIG["labels"]))
        self.assertEqual(sum(d.startswith("created record") for d in done), len(CONFIG["records"]))
        self.assertTrue(all(i["body"].startswith("<!-- role:builder run:run-1 -->") for i in repo.issues))
        self.assertEqual(tracker.problems(repo, CONFIG), [])
        self.assertEqual(tracker.setup(repo, CONFIG, CALLER), [])
        self.assertIn("bug", repo.labels)

    def test_setup_corrects_a_changed_label(self):
        repo = FakeRepo()
        tracker.setup(repo, CONFIG, CALLER)
        repo.labels["sev:1"]["color"] = "000000"
        self.assertEqual(tracker.problems(repo, CONFIG), ["label sev:1 differs in colour or description"])
        self.assertEqual(tracker.setup(repo, CONFIG, CALLER), ["updated label sev:1"])

    def test_a_duplicated_record_is_a_problem(self):
        repo = FakeRepo()
        tracker.setup(repo, CONFIG, CALLER)
        repo.issues.append({"number": 99, "labels": [{"name": "record:watch-list"}]})
        self.assertEqual(
            tracker.problems(repo, CONFIG), ["record record:watch-list: expected one open issue, found #2, #99"]
        )


if __name__ == "__main__":
    unittest.main()
