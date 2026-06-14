import logging

class LoadBalancer:
    def __init__(self, backend_service):
        self.logger = logging.getLogger("LoadBalancer")
        self.backend_service = backend_service

    def select_backend(self):
        """Select the best backend server based on load score."""
        alive_backends = self.backend_service.get_alive_backends()
        if not alive_backends:
            self.logger.error("No alive backends available for selection.")
            return None

        best_server_id = None
        min_score = float('inf')

        for server_id, info in alive_backends.items():
            # score = active_rooms * 10 + active_clients + active_transfers * 2
            score = (info.get("active_rooms", 0) * 10 + 
                     info.get("active_clients", 0) + 
                     info.get("active_transfers", 0) * 2)
            
            self.logger.debug(f"Backend {server_id} score: {score}")
            
            if score < min_score:
                min_score = score
                best_server_id = server_id
            elif score == min_score:
                # Basic round-robin-ish fallback if scores are equal
                # (Simple server_id comparison or random could work here)
                if best_server_id is None or server_id < best_server_id:
                    best_server_id = server_id

        return best_server_id
