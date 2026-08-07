from crawler.bioportal_client import BioPortalClient
from crawler.queue_manager import QueueManager
from crawler.graph_builder import GraphBuilder


class BFSCrawler:

    def __init__(self):
        self.client = BioPortalClient()
        self.queue = QueueManager()
        self.builder = GraphBuilder()

    def crawl(self, start_concept):

        self.queue.add(start_concept)

        while not self.queue.empty():

            concept_id = self.queue.pop()

            print(f"\nCrawling {concept_id}")

            concept = self.client.get_concept(concept_id)

            if concept is None:
                print("Concept not found.")
                continue

            self.builder.build(concept)


if __name__ == "__main__":

    crawler = BFSCrawler()

    crawler.crawl("233604007")