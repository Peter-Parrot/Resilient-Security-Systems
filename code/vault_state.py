"""Shared state and atomic checkpoints for a single active, passive-standby service."""
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time

DEFAULT_STATE_FILE = Path(__file__).resolve().parent / "state" / "vault.json"

INITIAL_KEYS = {
    "key1": {
        "algorithm": "RSA-2048",
        "fingerprint_sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        "payload": "-----BEGIN CERTIFICATE-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAkey1...\n-----END CERTIFICATE-----"
    },
    "key2": {
        "algorithm": "RSA-2048",
        "fingerprint_sha256": "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
        "payload": "-----BEGIN CERTIFICATE-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAkey2...\n-----END CERTIFICATE-----"
    },
    "key3": {
        "algorithm": "ECDSA-P256",
        "fingerprint_sha256": "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3",
        "payload": "-----BEGIN CERTIFICATE-----\nMIIBHgJBANB9...ECDSA...==\n-----END CERTIFICATE-----"
    }
}


class VaultState:
    def __init__(self, path=None, require_existing=False):
        self.path = Path(path or os.environ.get("KEY_SERVICE_STATE_FILE", DEFAULT_STATE_FILE))
        self.lock = threading.RLock()
        if self.path.exists():
            with self.path.open(encoding="utf-8") as stream:
                self.state = json.load(stream)
            self._validate()
            logging.info("Restored checkpoint: version=%d, keys=%d, encryptions=%d",
                         self.state["version"], len(self.state["keys"]),
                         self.state["encryption_count"])
        elif require_existing:
            raise ValueError(f"Required checkpoint is missing: {self.path}")
        else:
            self.state = {"schema_version": 1, "version": 0,
                          "keys": copy.deepcopy(INITIAL_KEYS), "encryption_count": 0}
            self.checkpoint()

    def _validate(self):
        state = self.state
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise ValueError("Unsupported checkpoint schema")
        for field in ("version", "encryption_count"):
            if type(state.get(field)) is not int or state[field] < 0:
                raise ValueError(f"Invalid checkpoint {field}")
        if state["version"] != state["encryption_count"]:
            raise ValueError("Inconsistent checkpoint counters")
        keys = state.get("keys")
        if not isinstance(keys, dict) or not INITIAL_KEYS.keys() <= keys.keys():
            raise ValueError("Invalid checkpoint key index")
        for key_id, record in keys.items():
            if not isinstance(record, dict) or not all(
                isinstance(record.get(field), str)
                for field in ("algorithm", "fingerprint_sha256", "payload")
            ):
                raise ValueError(f"Invalid checkpoint record: {key_id}")
        if len(keys) != len(INITIAL_KEYS) + state["encryption_count"]:
            raise ValueError("Checkpoint key count does not match encryption count")

    def checkpoint(self):
        """Replace only complete snapshots; a killed writer leaves the previous file intact."""
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                        dir=self.path.parent, prefix=".vault-", delete=False) as stream:
                    temporary = stream.name
                    json.dump(self.state, stream, sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                temporary = None
            finally:
                if temporary is not None:
                    os.unlink(temporary)

    def periodic_checkpoints(self, interval=5):
        while True:
            time.sleep(interval)
            try:
                self.checkpoint()
            except OSError:
                logging.exception("Periodic checkpoint failed")

    def handle_request(self, request):
        response = {"request_id": None, "timestamp": time.time()}
        def error(code, message):
            return dict(response, status="ERROR", error=code, message=message)
        if not isinstance(request, dict):
            return error("INVALID_REQUEST", "Request must be a JSON object.")
        response.update(request_id=request.get("request_id"), action=request.get("action"))
        action = request.get("action")
        key_id = request.get("key_id")
        if key_id is not None and not isinstance(key_id, str):
            return error("INVALID_REQUEST", "key_id must be a string.")
        with self.lock:
            if action == "ENCRYPT_KEY":
                client_id, request_id = request.get("client_id"), request.get("request_id")
                plaintext = request.get("plaintext_hash", "default-dummy-hash")
                if not all(isinstance(v, str) and v for v in (client_id, request_id, plaintext)):
                    return error("INVALID_REQUEST", "ENCRYPT_KEY requires nonempty client_id, request_id and plaintext_hash strings.")
                identity = json.dumps([client_id, request_id], separators=(",", ":"))
                key_id = "session-" + hashlib.sha256(identity.encode()).hexdigest()
                payload = f"ENCRYPTED_BLOB::{plaintext}::SECURE"
                record = {"algorithm": "AES-256-GCM",
                          "fingerprint_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                          "payload": payload}
                previous = self.state["keys"].get(key_id)
                if previous is not None and previous != record:
                    return error("REQUEST_ID_CONFLICT", "Request ID was already used with different input.")
                if previous is None:
                    before = copy.deepcopy(self.state)
                    self.state["keys"][key_id] = record
                    self.state["version"] += 1
                    self.state["encryption_count"] += 1
                    try:
                        # Commit before acknowledging; retries recover the same key ID.
                        self.checkpoint()
                    except OSError:
                        self.state = before
                        logging.exception("Cannot commit state change")
                        return error("CHECKPOINT_FAILED", "State change was not committed; retry the request.")
                response.update(key_id=key_id, data=record)
            elif action == "GET_CERT":
                if not key_id:
                    return error("MISSING_KEY_ID", "GET_CERT requires key_id.")
                if key_id not in self.state["keys"]:
                    return error("KEY_NOT_FOUND", f"Key ID '{key_id}' does not exist in vault.")
                response.update(key_id=key_id, data=self.state["keys"][key_id])
            elif action == "LIST_KEYS":
                payload = json.dumps(list(self.state["keys"]))
                response["data"] = {"algorithm": "INDEX", "payload": payload,
                                    "fingerprint_sha256": hashlib.sha256(payload.encode()).hexdigest()}
            elif action == "GET_STATE":
                response["data"] = {"version": self.state["version"],
                                    "encryption_count": self.state["encryption_count"],
                                    "key_count": len(self.state["keys"])}
            else:
                return error("UNKNOWN_ACTION" if action else "INVALID_REQUEST", "Unsupported or missing action.")
            response.update(status="OK", state_version=self.state["version"])
            return copy.deepcopy(response)
