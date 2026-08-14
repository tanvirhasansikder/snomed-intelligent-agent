import pickle
from pathlib import Path


class StateManager:
    """
    Saves and restores the crawler state.
    """

    STATE_DIR = Path("crawler_state")

    QUEUE_FILE = STATE_DIR / "queue.pkl"
    VISITED_FILE = STATE_DIR / "visited.pkl"

    def __init__(self):

        self.STATE_DIR.mkdir(exist_ok=True)

    def save(self, queue, visited):

        with open(self.QUEUE_FILE, "wb") as f:
            pickle.dump(queue, f)

        with open(self.VISITED_FILE, "wb") as f:
            pickle.dump(visited, f)

        print("💾 Crawler state saved.")

    def load(self):

        if (
            not self.QUEUE_FILE.exists()
            or
            not self.VISITED_FILE.exists()
        ):
            return None

        with open(self.QUEUE_FILE, "rb") as f:
            queue = pickle.load(f)

        with open(self.VISITED_FILE, "rb") as f:
            visited = pickle.load(f)

        print("📂 Previous crawler state loaded.")

        return queue, visited

    def clear(self):

        if self.QUEUE_FILE.exists():
            self.QUEUE_FILE.unlink()

        if self.VISITED_FILE.exists():
            self.VISITED_FILE.unlink()

        print("🗑 Saved state removed.")


if __name__ == "__main__":

    from collections import deque

    state = StateManager()

    queue = deque([
        ("233604007", 0),
        ("205237003", 1)
    ])

    visited = {
        "233604007",
        "205237003"
    }

    state.save(queue, visited)

    restored = state.load()

    print(restored)

    state.clear()