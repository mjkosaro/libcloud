# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance withv
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from libcloud.common.dimensiondata import DimensionDataConnection
from libcloud.common.dimensiondata import DimensionDataPool
from libcloud.common.dimensiondata import DimensionDataPoolMember
from libcloud.common.dimensiondata import API_ENDPOINTS
from libcloud.common.dimensiondata import DEFAULT_REGION
from libcloud.common.dimensiondata import TYPES_URN
from libcloud.common.dimensiondata import SERVER_NS
from libcloud.utils.misc import find, reverse_dict
from libcloud.utils.xml import fixxpath, findtext, findall
from libcloud.loadbalancer.types import State
from libcloud.loadbalancer.base import Algorithm, Driver, LoadBalancer
from libcloud.loadbalancer.base import DEFAULT_ALGORITHM, Member
from libcloud.loadbalancer.types import Provider

class DimensionDataLBDriver(Driver):
    """
    DimensionData node driver.
    """

    selected_region = None
    connectionCls = DimensionDataConnection
    name = 'Dimension Data Load Balancer'
    website = 'https://cloud.dimensiondata.com/'
    type = Provider.DIMENSIONDATA
    api_version = 1.0

    _VALUE_TO_ALGORITHM_MAP = {
        'ROUND_ROBIN': Algorithm.ROUND_ROBIN,
        'LEAST_CONNECTIONS': Algorithm.LEAST_CONNECTIONS,
        'SHORTEST_RESPONSE': Algorithm.SHORTEST_RESPONSE,
        'PERSISTENT_IP': Algorithm.PERSISTENT_IP
    }
    _ALGORITHM_TO_VALUE_MAP = reverse_dict(_VALUE_TO_ALGORITHM_MAP)

    _VALUE_TO_STATE_MAP = {
        'NORMAL': State.RUNNING,
        'PENDING_ADD': State.PENDING,
        'PENDING_CHANGE': State.PENDING,
        'PENDING_DELETE': State.PENDING,
        'FAILED_ADD': State.ERROR,
        'FAILED_CHANGE': State.ERROR,
        'FAILED_DELETE': State.ERROR,
        'REQUIRES_SUPPORT': State.ERROR
    }

    def __init__(self, key, secret=None, secure=True, host=None, port=None,
                 api_version=None, region=DEFAULT_REGION, **kwargs):

        if region not in API_ENDPOINTS:
            raise ValueError('Invalid region: %s' % (region))

        self.selected_region = API_ENDPOINTS[region]

        super(DimensionDataLBDriver, self).__init__(key=key, secret=secret,
                                                      secure=secure, host=host,
                                                      port=port,
                                                      api_version=api_version,
                                                      region=region,
                                                      **kwargs)

    def _ex_connection_class_kwargs(self):
        """
            Add the region to the kwargs before the connection is instantiated
        """

        kwargs = super(DimensionDataLBDriver,
                       self)._ex_connection_class_kwargs()
        kwargs['region'] = self.selected_region
        return kwargs

    def create_balancer(self, name, port, protocol, algorithm, members):
        """
        Create a new load balancer instance

        :param name: Name of the new load balancer (required)
        :type  name: ``str``

        :param port: Port the load balancer should listen on, defaults to 80
        :type  port: ``str``

        :param protocol: Loadbalancer protocol, defaults to http.
        :type  protocol: ``str``

        :param members: list of Members to attach to balancer
        :type  members: ``list`` of :class:`Member`

        :param algorithm: Load balancing algorithm, defaults to ROUND_ROBIN.
        :type algorithm: :class:`.Algorithm`

        :rtype: :class:`LoadBalancer`
        """
        # TODO: Figure out which location is used...
        network_domain_id=None
        
        # Create a pool first 
        pool_id=self.ex_create_pool(
            network_domain_id=network_domain_id,
            name=name,
            ex_description=None,
            balancer_method=_ALGORITHM_TO_VALUE_MAP.get(algorithm)
            )
        
        # Attach the members to the pool as nodes
        for member in members:
            node_id=self.ex_create_node(
                network_domain_id=network_domain_id,
                name=member.name,
                ip=member.ip,
                ex_description=None
                )
            self.ex_create_pool_member(
                pool_id=pool_id,
                node_id=node_id,
                port=port)
            
        # Create the virtual listener (balancer)
        self.ex_create_virtual_listener(
                       network_domain_id=network_domain_id,
                       name=name,
                       ex_description=None,
                       port=port)
        return True

    def list_balancers(self):
        """
        List all loadbalancers inside a geography.
        
        In Dimension Data terminology these are known as virtual listeners

        :rtype: ``list`` of :class:`LoadBalancer`
        """
        
        return self._to_balancers(
            self.connection
            .request_with_orgId_api_2('networkDomainVip/virtualListener').object)

    def get_balancer(self, balancer_id):
        """
        Return a :class:`LoadBalancer` object.

        :param balancer_id: id of a load balancer you want to fetch
        :type  balancer_id: ``str``

        :rtype: :class:`LoadBalancer`
        """
        
        bal = self.connection \
            .request_with_orgId_api_2('networkDomainVip/virtualListener/%s'
                                      % balancer_id).object
        return self._to_balancer(bal)

    def list_protocols(self):
        """
        Return a list of supported protocols.

        Since all protocols are support by Dimension Data, this is a list
        of common protocols. 

        :rtype: ``list`` of ``str``
        """
        return ['http', 'https', 'tcp', 'udp']

    def balancer_list_members(self, balancer):
        """
        Return list of members attached to balancer.
        
        In Dimension Data terminology these are the members of the pools
        within a virtual listener.

        :param balancer: LoadBalancer which should be used
        :type  balancer: :class:`LoadBalancer`

        :rtype: ``list`` of :class:`Member`
        """
        
        pool_members = self.ex_get_pool_members(balancer.extra['pool_id'])
        members = []
        for pool_member in pool_members:
            members.append(Member(
                id=pool_member.id,
                ip=pool_member.ip_address,
                port=pool_member.port,
                balancer=balancer,
                extra=None
            ))
        return members

    def balancer_attach_member(self, balancer, member):
        """
        Attach a member to balancer

        :param balancer: LoadBalancer which should be used
        :type  balancer: :class:`LoadBalancer`

        :param member: Member to join to the balancer
        :type member: :class:`Member`

        :return: Member after joining the balancer.
        :rtype: :class:`Member`
        """
        
        nodeId= self.ex_create_node(
            network_domain_id=balancer.extra['network_domain_id'],
            name=member.name,
            ip=member.ip,
            ex_description=member.description
        )
        if nodeId == false:
            return False
        
        poolMemberId = self.ex_create_pool_member(balancer.extra['pool_id'],
                                                  nodeId,
                                                  member.port)
        
        return True

    def balancer_detach_member(self, balancer, member):
        """
        Detach member from balancer

        :param balancer: LoadBalancer which should be used
        :type  balancer: :class:`LoadBalancer`

        :param member: Member which should be used
        :type member: :class:`Member`

        :return: ``True`` if member detach was successful, otherwise ``False``.
        :rtype: ``bool``
        """
        
        create_pool_m = ET.Element('removePoolMember', {'xmlns': SERVER_NS,
                                                        'id': member.id})

        self.connection.request_with_orgId_api_2(
            'networkDomainVip/removePoolMember',
            method='POST',
            data=ET.tostring(create_pool_m)).object
        
        return True

    def destroy_balancer(self, balancer):
        """
        Destroy a load balancer (virtual listener)

        :param balancer: LoadBalancer which should be used
        :type  balancer: :class:`LoadBalancer`

        :return: ``True`` if the destroy was successful, otherwise ``False``.
        :rtype: ``bool``
        """
        
        delete_listener = ET.Element('deleteVirtualListener', {'xmlns': SERVER_NS,
                                                               'id': balancer.id})

        self.connection.request_with_orgId_api_2(
            'networkDomainVip/deleteVirtualListener',
            method='POST',
            data=ET.tostring(delete_listener)).object
        return True

    def ex_create_pool_member(self, pool_id, node_id, port):
        """
        Create a new member in an existing pool from an existing node

        :param pool_id: ID of the pool (required)
        :type  name: ``str``

        :param node_id: ID of the node (required)
        :type  name: ``str``

        :param port: Port the the service will listen on
        :type  port: ``str``

        :return: ID of the node member, ``False`` if failed to add. 
        :rtype: ``str``
        """
        create_pool_m = ET.Element('addPoolMember', {'xmlns': SERVER_NS})
        ET.SubElement(create_pool_m, "poolId").text = pool_id
        ET.SubElement(create_pool_m, "nodeId").text = node_id
        ET.SubElement(create_pool_m, "port").text = port

        response = self.connection.request_with_orgId_api_2(
            'networkDomainVip/addPoolMember',
            method='POST',
            data=ET.tostring(create_pool_m)).object
        
        for info in findall(response, 'info', TYPES_URN):
            if info.name == 'poolMemberId':
                return info.value
        
        return False

    def ex_create_node(self,
                       network_domain_id,
                       name,
                       ip,
                       ex_description,
                       connection_limit=25000,
                       connection_rate_limit=2000):
        """
        Create a new node

        :param network_domain_id: Network Domain ID (required)
        :type  name: ``str``

        :param name: name of the node (required)
        :type  name: ``str``

        :param ip: IPv4 address of the node (required)
        :type  ip: ``str``

        :param ex_description: Description of the node
        :type  ex_description: ``str``
        
        :param connection_limit: Maximum number of concurrent connections per sec
        :type  connection_limit: ``int``
        
        :param connection_rate_limit: Maximum number of concurrent sessions
        :type  connection_rate_limit: ``int``

        :return: ID of the node, ``False`` if failed to add. 
        :rtype: ``str``
        """
        create_node_elm = ET.Element('createNode', {'xmlns': SERVER_NS})
        ET.SubElement(create_node_elm, "networkDomainId").text = network_domain_id
        ET.SubElement(create_node_elm, "description").text = ex_description
        ET.SubElement(create_node_elm, "name").text = name
        ET.SubElement(create_node_elm, "ipv4Address").text = ip
        ET.SubElement(create_node_elm, "connectionLimit").text = connection_limit
        ET.SubElement(create_node_elm, "connectionRateLimit").text = connection_rate_limit

        response = self.connection.request_with_orgId_api_2(
            'networkDomainVip/createNode',
            method='POST',
            data=ET.tostring(create_node_elm)).object
        
        for info in findall(response, 'info', TYPES_URN):
            if info.name == 'nodeId':
                return info.value
        
        return None

    def ex_create_pool(self,
                       network_domain_id,
                       name,
                       balancer_method,
                       ex_description,
                       service_down_action='NONE',
                       slow_ramp_time=30):
        """
        Create a new pool

        :param network_domain_id: Network Domain ID (required)
        :type  name: ``str``

        :param name: name of the node (required)
        :type  name: ``str``

        :param balancer_method: The load balancer algorithm (required)
        :type  balancer_method: ``str``

        :param ex_description: Description of the node
        :type  ex_description: ``str``
        
        :param service_down_action: What to do when node is unavailable NONE, DROP or RESELECT
        :type  service_down_action: ``str``
        
        :param slow_ramp_time: Number of seconds to stagger ramp up of nodes
        :type  slow_ramp_time: ``int``

        :return: ID of the pool, ``False`` if failed to add. 
        :rtype: ``str``
        """
        create_node_elm = ET.Element('createPool', {'xmlns': SERVER_NS})
        ET.SubElement(create_node_elm, "networkDomainId").text = network_domain_id
        ET.SubElement(create_node_elm, "description").text = ex_description
        ET.SubElement(create_node_elm, "name").text = name
        ET.SubElement(create_node_elm, "loadBalancerMethod").text = balancer_method
        ET.SubElement(create_node_elm, "serviceDownAction").text = service_down_action
        ET.SubElement(create_node_elm, "slowRampTime").text = slow_ramp_time

        response = self.connection.request_with_orgId_api_2(
            'networkDomainVip/createPool',
            method='POST',
            data=ET.tostring(create_node_elm)).object
        
        for info in findall(response, 'info', TYPES_URN):
            if info.name == 'poolId':
                return info.value
        
        return False

    def ex_create_virtual_listener(self,
                       network_domain_id,
                       name,
                       ex_description,
                       port,
                       listener_type='STANDARD',
                       protocol='ANY',
                       connection_limit=25000,
                       connection_rate_limit=2000,
                       source_port_preservation='PRESERVE'):
        create_node_elm = ET.Element('createVirtualListener', {'xmlns': SERVER_NS})
        ET.SubElement(create_node_elm, "networkDomainId").text = network_domain_id
        ET.SubElement(create_node_elm, "description").text = ex_description
        ET.SubElement(create_node_elm, "name").text = name
        ET.SubElement(create_node_elm, "port").text = port
        ET.SubElement(create_node_elm, "type").text = listener_type
        ET.SubElement(create_node_elm, "connectionLimit").text = connection_limit
        ET.SubElement(create_node_elm, "connectionRateLimit").text = connection_rate_limit
        ET.SubElement(create_node_elm, "sourcePortPreservation").text = source_port_preservation

        response = self.connection.request_with_orgId_api_2(
            'networkDomainVip/createVirtualListener',
            method='POST',
            data=ET.tostring(create_node_elm)).object
        
        for info in findall(response, 'info', TYPES_URN):
            if info.name == 'poolId':
                return info.value
        
        return False

    def ex_get_pools(self):
        pools = self.connection \
            .request_with_orgId_api_2('networkDomainVip/pool').object
        return self._to_pools(pools)

    def ex_get_pool(self, pool_id):
        pool = self.connection \
            .request_with_orgId_api_2('networkDomainVip/pool/%s'
                                      % pool_id).object
        return self._to_pool(pool)
    
    def ex_get_pool_members(self, pool_id):
        members = self.connection \
            .request_with_orgId_api_2('networkDomainVip/poolMember?poolId=%s'
                                      % pool_id).object
        return self._to_members(members)
    
    def ex_get_pool_member(self, pool_member_id):
        member = self.connection \
            .request_with_orgId_api_2('networkDomainVip/poolMember/%s'
                                      % pool_member_id).object
        return self._to_member(member)

    def _to_balancers(self, object ):
        loadbalancers = []
        for element in object.findall(fixxpath("virtualListener", TYPES_URN)):
            loadbalancers.append(self._to_balancer(element))

        return loadbalancers

    def _to_balancer(self, element):
        ipaddress = findtext(element, 'listenerIpAddress', TYPES_URN)
        name = findtext(element, 'name', TYPES_URN)
        port = findtext(element, 'port', TYPES_URN)
        extra = {}
        
        extra['pool_id'] = element.find(fixxpath(
                'pool',
                TYPES_URN)).get('id')
        extra['network_domain_id'] = findtext(element, 'networkDomainId',
                                              TYPES_URN)
        
        balancer = LoadBalancer(
            id=element.get('id'),
            name=name,
            state=self._VALUE_TO_STATE_MAP.get(
                              findtext(element, 'state', TYPES_URN),
                              State.UNKNOWN),
            ip=ipaddress,
            port=port,
            driver=self.connection.driver,
            extra=extra
        )

        return balancer
    
    def _to_members(self, object ):
        members = []
        for element in object.findall(fixxpath("poolMember", TYPES_URN)):
            members.append(self._to_member(element))

        return members
    
    def _to_member(self, element):
        pool = DimensionDataPoolMember(
            id=element.get('id'),
            name=element.find(fixxpath(
                'node',
                TYPES_URN)).get('name'),
            status=findtext(element, 'state', TYPES_URN),
            ip_address=element.find(fixxpath(
                'node',
                TYPES_URN)).get('ipAddress'),
            port=int(findtext(element, 'port', TYPES_URN))
        )
        return pool
    
    def _to_pools(self, object ):
        pools = []
        for element in object.findall(fixxpath("pool", TYPES_URN)):
            pools.append(self._to_pool(element))

        return pools
    
    def _to_pool(self, element):
        pool = DimensionDataPool(
            id=element.get('id'),
            name=findtext(element, 'name', TYPES_URN),
            status=findtext(element,'state', TYPES_URN),
            description=findtext(element, 'description', TYPES_URN)
        )
        return pool
