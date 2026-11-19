"""Cifrado autenticado de archivos de proyecto colaborativo."""
from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidTag


class InvalidProjectPasswordError(ValueError):
    """La contraseña no permite autenticar el proyecto."""


class ProjectCrypto:
    """AES-256-GCM con clave derivada por PBKDF2-HMAC-SHA256."""

    ITERATIONS = 480_000
    KIND = "aes-256-gcm-pbkdf2-sha256"

    def __init__(self, key: bytes, salt: bytes) -> None:
        """Conserva una clave de sesión derivada."""
        self.aes = AESGCM(key)
        self.salt = salt

    @classmethod
    def create(cls, password: str) -> ProjectCrypto:
        """Deriva una clave nueva desde una contraseña."""
        if not password:
            raise ValueError("Indica una contraseña para el proyecto colaborativo.")
        salt = os.urandom(16)
        return cls(cls._derive(password, salt), salt)

    @classmethod
    def from_metadata(
        cls, password: str, metadata: object
    ) -> ProjectCrypto:
        """Recupera la clave usando metadatos públicos del manifiesto."""
        if not isinstance(metadata, dict):
            raise ValueError("El proyecto no contiene metadatos de cifrado válidos.")
        if metadata.get("kind") != cls.KIND:
            raise ValueError("El cifrado del proyecto no es compatible.")
        try:
            salt = urlsafe_b64decode(str(metadata["salt"]))
            iterations = int(metadata.get("iterations", 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Los metadatos de cifrado no son válidos.") from exc
        if iterations != cls.ITERATIONS or len(salt) != 16:
            raise ValueError("Los parámetros de cifrado no son compatibles.")
        return cls(cls._derive(password, salt), salt)

    @staticmethod
    def _derive(password: str, salt: bytes) -> bytes:
        """Deriva 256 bits sin guardar ni reutilizar la contraseña."""
        return PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=ProjectCrypto.ITERATIONS,
        ).derive(password.encode("utf-8"))

    @property
    def metadata(self) -> dict[str, object]:
        """Devuelve solo parámetros públicos necesarios para volver a abrir."""
        return {
            "kind": self.KIND,
            "iterations": self.ITERATIONS,
            "salt": urlsafe_b64encode(self.salt).decode("ascii"),
        }

    def encrypt(self, payload: Any) -> dict[str, str]:
        """Cifra y autentica un objeto JSON."""
        nonce = os.urandom(12)
        plaintext = json.dumps(
            payload, ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
        ciphertext = self.aes.encrypt(nonce, plaintext, b"gestor-proyectos-v3")
        return {
            "format": "gdp-encrypted-json-v1",
            "nonce": urlsafe_b64encode(nonce).decode("ascii"),
            "ciphertext": urlsafe_b64encode(ciphertext).decode("ascii"),
        }

    def encrypt_bytes(self, plaintext: bytes) -> dict[str, str]:
        """Cifra bytes binarios autenticándolos con la misma clave."""
        nonce = os.urandom(12)
        ciphertext = self.aes.encrypt(
            nonce, plaintext, b"gestor-proyectos-binary-v3"
        )
        return {
            "format": "gdp-encrypted-binary-v1",
            "nonce": urlsafe_b64encode(nonce).decode("ascii"),
            "ciphertext": urlsafe_b64encode(ciphertext).decode("ascii"),
        }

    def decrypt(self, envelope: object) -> Any:
        """Autentica y descifra un objeto; la contraseña incorrecta falla."""
        if (
            not isinstance(envelope, dict)
            or envelope.get("format") != "gdp-encrypted-json-v1"
        ):
            raise ValueError("El archivo no contiene un JSON cifrado compatible.")
        try:
            nonce = urlsafe_b64decode(str(envelope["nonce"]))
            ciphertext = urlsafe_b64decode(str(envelope["ciphertext"]))
            plaintext = self.aes.decrypt(
                nonce, ciphertext, b"gestor-proyectos-v3"
            )
            return json.loads(plaintext.decode("utf-8"))
        except (InvalidTag, UnicodeError, ValueError, KeyError) as exc:
            raise InvalidProjectPasswordError(
                "Contraseña incorrecta o archivo cifrado modificado."
            ) from exc

    def decrypt_bytes(self, envelope: object) -> bytes:
        """Descifra un recurso binario y valida su autenticidad."""
        if (
            not isinstance(envelope, dict)
            or envelope.get("format") != "gdp-encrypted-binary-v1"
        ):
            raise ValueError("El recurso cifrado no tiene un formato compatible.")
        try:
            return self.aes.decrypt(
                urlsafe_b64decode(str(envelope["nonce"])),
                urlsafe_b64decode(str(envelope["ciphertext"])),
                b"gestor-proyectos-binary-v3",
            )
        except (InvalidTag, KeyError, ValueError) as exc:
            raise InvalidProjectPasswordError(
                "Contraseña incorrecta o biblioteca cifrada modificada."
            ) from exc
