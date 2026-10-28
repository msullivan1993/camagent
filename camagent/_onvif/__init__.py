"""onvif-zeep 0.2.12 (MIT, Quatanium; LICENSE here), kept inside camagent since 0.17: PyPI has it only as source
code, which pip would have to build on every host with unchecked build tools. Signed releases now install only
files whose checksums the release lists (requirements.lock), so it comes with camagent instead. Unchanged except
for relative imports (and the unused onvif-cli left out). Its WSDL files are camagent's own wsdl folder."""
from .client import ONVIFService, ONVIFCamera, SERVICES
from .exceptions import ONVIFError, ERR_ONVIF_UNKNOWN, \
        ERR_ONVIF_PROTOCOL, ERR_ONVIF_WSDL, ERR_ONVIF_BUILD
#from onvif import cli

__all__ = ( 'ONVIFService', 'ONVIFCamera', 'ONVIFError',
            'ERR_ONVIF_UNKNOWN', 'ERR_ONVIF_PROTOCOL',
            'ERR_ONVIF_WSDL', 'ERR_ONVIF_BUILD',
            'SERVICES'#, 'cli'
           )
