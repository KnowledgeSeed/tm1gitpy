from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Dict, Optional
import yaml


logger = logging.getLogger(__name__)


@dataclass
class TM1ServerConfig:
    """Configuration for a single TM1 server connection."""
    
    name: str
    base_url: str
    user: str
    password: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert configuration to dictionary."""
        return {
            'name': self.name,
            'base_url': self.base_url,
            'user': self.user,
            'password': self.password
        }
    
    def __repr__(self) -> str:
        """String representation (hides password)."""
        return (f"TM1ServerConfig(name='{self.name}', base_url='{self.base_url}', "
                f"user='{self.user}')")


class TM1ServersConfig:
    """Manager for multiple TM1 server configurations."""
    
    @staticmethod
    def _get_default_config_path() -> Path:
        """Get default configuration path, checking local directory first, then user home.
        
        Returns:
            Path to configuration file (local if exists, otherwise home directory)
        """
        local_path = Path('.tm1gitpy') / 'tm1servers.yaml'
        home_path = Path.home() / '.tm1gitpy' / 'tm1servers.yaml'
        
        # Return local path if it exists, otherwise return home path
        return local_path if local_path.exists() else home_path
    
    def __init__(self, config_path: Optional[Path] = None):
        """Initialize the configuration manager.
        
        Args:
            config_path: Path to the YAML configuration file. 
                        Defaults to .tm1gitpy/tm1servers.yaml (local) or ~/.tm1gitpy/tm1servers.yaml (home)
        """
        self.config_path = config_path or self._get_default_config_path()
        self.servers: Dict[str, TM1ServerConfig] = {}
    
    def load(self) -> Dict[str, TM1ServerConfig]:
        """Load server configurations from YAML file.
        
        Returns:
            Dictionary mapping server names to TM1ServerConfig objects
            
        Raises:
            FileNotFoundError: If the configuration file doesn't exist
            yaml.YAMLError: If the YAML file is invalid
            ValueError: If the configuration structure is invalid
        """
        if not self.config_path.exists():
            logger.error("Configuration file not found: %s", self.config_path)
            raise FileNotFoundError(
                f"Configuration file not found: {self.config_path}\n"
                f"Please create it with your TM1 server configurations."
            )

        logger.info("Loading TM1 server configuration from '%s'", self.config_path)
        with open(self.config_path, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f)
        
        if not config_data:
            raise ValueError("Configuration file is empty")
        
        if 'servers' not in config_data:
            raise ValueError(
                "Invalid configuration structure. Expected 'servers' key at root level."
            )
        
        servers_data = config_data['servers']
        if not isinstance(servers_data, dict):
            raise ValueError("'servers' must be a dictionary")
        
        self.servers = {}
        for name, server_config in servers_data.items():
            try:
                self.servers[name] = TM1ServerConfig(
                    name=name,
                    base_url=server_config['base_url'],
                    user=server_config['user'],
                    password=server_config.get('password') or None
                )
            except KeyError as e:
                raise ValueError(
                    f"Missing required field {e} for server '{name}'"
                ) from e
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"Invalid value in configuration for server '{name}': {e}"
                ) from e

        logger.info("Loaded %d TM1 server configuration(s)", len(self.servers))

        return self.servers
    
    def get(self, server_name: str) -> TM1ServerConfig:
        """Get configuration for a specific server.
        
        Args:
            server_name: Name of the server
            
        Returns:
            TM1ServerConfig object
            
        Raises:
            KeyError: If server name not found
        """
        if not self.servers:
            self.load()
        
        if server_name not in self.servers:
            available = ', '.join(self.servers.keys())
            logger.error("Requested unknown server '%s'. Available: %s", server_name, available)
            raise KeyError(
                f"Server '{server_name}' not found in configuration. "
                f"Available servers: {available}"
            )

        logger.debug("Resolved TM1 server configuration for '%s'", server_name)
        return self.servers[server_name]
    
    def list_servers(self) -> list[str]:
        """Get list of all configured server names.
        
        Returns:
            List of server names
        """
        if not self.servers:
            self.load()
        return list(self.servers.keys())
    
    @classmethod
    def from_file(cls, config_path: Optional[Path] = None) -> 'TM1ServersConfig':
        """Create and load configuration from file.
        
        Args:
            config_path: Path to the YAML configuration file
            
        Returns:
            Loaded TM1ServersConfig instance
        """
        config_manager = cls(config_path)
        config_manager.load()
        return config_manager


def load_tm1_servers(config_path: Optional[Path] = None) -> Dict[str, TM1ServerConfig]:
    """Convenience function to load TM1 server configurations.
    
    Args:
        config_path: Path to the YAML configuration file. 
                    Defaults to .tm1gitpy/tm1servers.yaml (local directory if exists,
                    otherwise ~/.tm1gitpy/tm1servers.yaml in user home)
    
    Returns:
        Dictionary mapping server names to TM1ServerConfig objects
        
    Example:
        >>> servers = load_tm1_servers()
        >>> dev_server = servers['dev']
        >>> print(dev_server.base_url, dev_server.user)
    """
    config_manager = TM1ServersConfig(config_path)
    return config_manager.load()
