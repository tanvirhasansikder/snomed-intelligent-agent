from collections import deque


class QueueManager:
    def __init__(self):
        self.queue = deque()
        self.visited = set()

    def add(self, concept_id):
        if concept_id not in self.visited:
            self.queue.append(concept_id)
            self.visited.add(concept_id)

    def pop(self):
        if self.queue:
            return self.queue.popleft()
        return None

    def empty(self):
        return len(self.queue) == 0


if __name__ == "__main__":
    print("Queue Manager Test")

    q = QueueManager()

    q.add("1")
    q.add("2")
    q.add("1")

    while not q.empty():
        print(q.pop())