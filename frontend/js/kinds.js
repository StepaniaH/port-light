/* Kind chip matchers over occupancy rows. */

export const KIND_MATCHERS = {
  running: function (p) {
    return p.containers && p.containers.some(function (c) {
      return c.status === 'running' || c.status === 'paused' || c.status === 'restarting';
    });
  },
  system: function (p) {
    return p.source_type === 'system' || (p.known_service && p.known_service.category === 'system');
  },
  docker: function (p) {
    return p.source_type === 'docker' || (p.containers && p.containers.length > 0);
  },
  access: function (p) {
    return p.known_service && p.known_service.is_access_port;
  },
  udp: function (p) {
    return (p.protocol || '').indexOf('udp') !== -1;
  },
  localhost: function (p) {
    return p.bind_scope === 'localhost';
  },
  public: function (p) {
    return p.bind_scope === 'public';
  },
  hidden: function (p) {
    return !!p.is_hidden;
  },
};
