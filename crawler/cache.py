class CacheManager:
    """
    Simple in-memory cache for BioPortal responses.
    """

    def __init__(self):
        self.cache = {}

    def exists(self, concept_id):
        """
        Check whether a concept is already cached.
        """
        return concept_id in self.cache

    def get(self, concept_id):
        """
        Retrieve a cached concept.
        """
        return self.cache.get(concept_id)

    def save(self, concept_id, concept_data):
        """
        Save a concept into the cache.
        """
        self.cache[concept_id] = concept_data

    def clear(self):
        """
        Clear the cache.
        """
        self.cache.clear()

    def size(self):
        """
        Number of cached concepts.
        """
        return len(self.cache)


if __name__ == "__main__":

    cache = CacheManager()

    cache.save(
        "233604007",
        {
            "name": "Pneumonia"
        }
    )

    print(cache.exists("233604007"))
    print(cache.get("233604007"))
    print("Cache Size:", cache.size())