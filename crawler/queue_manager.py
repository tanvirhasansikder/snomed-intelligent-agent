from collections import deque


class QueueManager:

    def __init__(self):
        self.queue = deque()
        self.visited = set()

    def add(self, concept_id, depth):
        """
        Add a concept to the queue if it has not been visited.
        """

        if concept_id not in self.visited:

            self.queue.append((concept_id, depth))
            self.visited.add(concept_id)

    def pop(self):
        """
        Remove the next concept from the queue.
        """

        if self.queue:
            return self.queue.popleft()

        return None

    def empty(self):
        return len(self.queue) == 0


if __name__ == "__main__":

    q = QueueManager()

    q.add("1", 0)
    q.add("2", 1)
    q.add("1", 5)

    while not q.empty():
        print(q.pop())