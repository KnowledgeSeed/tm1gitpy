"""Tests for TM1 server configuration management."""

import pytest
import tempfile
from pathlib import Path
import yaml

from tm1_git_py.config import (
    TM1ServerConfig,
    TM1ServersConfig,
    load_tm1_servers
)


def test_tm1_server_config_creation():
    """Test creating a TM1ServerConfig object."""
    config = TM1ServerConfig(
        name='test',
        base_url='http://localhost:12354/api/v1/',
        user='admin',
        password='secret'
    )
    
    assert config.name == 'test'
    assert config.base_url == 'http://localhost:12354/api/v1/'
    assert config.user == 'admin'
    assert config.password == 'secret'


def test_tm1_server_config_repr():
    """Test string representation doesn't expose password."""
    config = TM1ServerConfig(
        name='test',
        base_url='http://localhost:12354/api/v1/',
        user='admin',
        password='secret'
    )
    
    repr_str = repr(config)
    assert 'test' in repr_str
    assert 'localhost' in repr_str
    assert 'secret' not in repr_str  # Password should not be in repr


def test_tm1_server_config_to_dict():
    """Test converting config to dictionary."""
    config = TM1ServerConfig(
        name='test',
        base_url='http://localhost:12354/api/v1/',
        user='admin'
    )
    
    config_dict = config.to_dict()
    assert config_dict['name'] == 'test'
    assert config_dict['base_url'] == 'http://localhost:12354/api/v1/'
    assert config_dict['user'] == 'admin'


def test_load_valid_config():
    """Test loading a valid configuration file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / 'tm1servers.yaml'
        
        # Create test configuration
        config_data = {
            'servers': {
                'dev': {
                    'base_url': 'http://localhost:12354/api/v1/',
                    'user': 'admin'
                },
                'prod': {
                    'base_url': 'https://prod.server.com:12355/api/v1/',
                    'user': 'produser',
                    'password': 'secret'
                }
            }
        }
        
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)
        
        # Load configuration
        servers = load_tm1_servers(config_path=config_file)
        
        assert len(servers) == 2
        assert 'dev' in servers
        assert 'prod' in servers
        
        dev_server = servers['dev']
        assert dev_server.base_url == 'http://localhost:12354/api/v1/'
        assert dev_server.user == 'admin'
        assert dev_server.password is None
        
        prod_server = servers['prod']
        assert prod_server.base_url == 'https://prod.server.com:12355/api/v1/'
        assert prod_server.user == 'produser'
        assert prod_server.password == 'secret'


def test_load_missing_file():
    """Test loading non-existent configuration file."""
    with pytest.raises(FileNotFoundError):
        load_tm1_servers(config_path=Path('nonexistent.yaml'))


def test_load_empty_file():
    """Test loading empty configuration file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / 'empty.yaml'
        config_file.write_text('')
        
        with pytest.raises(ValueError, match="empty"):
            load_tm1_servers(config_path=config_file)


def test_load_invalid_structure():
    """Test loading configuration with invalid structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / 'invalid.yaml'
        
        # Missing 'servers' key
        config_data = {
            'dev': {
                'base_url': 'http://localhost:12354/api/v1/',
                'user': 'admin'
            }
        }
        
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)
        
        with pytest.raises(ValueError, match="servers"):
            load_tm1_servers(config_path=config_file)


def test_load_missing_required_field():
    """Test loading configuration with missing required fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / 'incomplete.yaml'
        
        # Missing 'user' field
        config_data = {
            'servers': {
                'dev': {
                    'base_url': 'http://localhost:12354/api/v1/'
                }
            }
        }
        
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)
        
        with pytest.raises(ValueError, match="user"):
            load_tm1_servers(config_path=config_file)


def test_tm1_servers_config_get():
    """Test getting a specific server from manager."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / 'tm1servers.yaml'
        
        config_data = {
            'servers': {
                'dev': {
                    'base_url': 'http://localhost:12354/api/v1/',
                    'user': 'admin'
                }
            }
        }
        
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)
        
        manager = TM1ServersConfig(config_path=config_file)
        manager.load()
        
        dev_server = manager.get('dev')
        assert dev_server.name == 'dev'


def test_tm1_servers_config_get_nonexistent():
    """Test getting non-existent server from manager."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / 'tm1servers.yaml'
        
        config_data = {
            'servers': {
                'dev': {
                    'base_url': 'http://localhost:12354/api/v1/',
                    'user': 'admin'
                }
            }
        }
        
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)
        
        manager = TM1ServersConfig(config_path=config_file)
        manager.load()
        
        with pytest.raises(KeyError, match="nonexistent"):
            manager.get('nonexistent')


def test_tm1_servers_config_list():
    """Test listing all servers."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / 'tm1servers.yaml'
        
        config_data = {
            'servers': {
                'dev': {
                    'base_url': 'http://localhost:12354/api/v1/',
                    'user': 'admin'
                },
                'prod': {
                    'base_url': 'https://prod.com:12355/api/v1/',
                    'user': 'admin'
                }
            }
        }
        
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)
        
        manager = TM1ServersConfig(config_path=config_file)
        servers = manager.list_servers()
        
        assert len(servers) == 2
        assert 'dev' in servers
        assert 'prod' in servers
