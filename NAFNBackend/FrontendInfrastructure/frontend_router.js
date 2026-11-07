// CDK replaces this placeholder with the configured domain at synthesis time.
var routingConfig = __ROUTING_CONFIG__;

function notFound() {
    return { statusCode: 404, statusDescription: 'Not Found' };
}

function handler(event) {
    var request = event.request;
    var host = request.headers.host;
    if (!host) return notFound();

    var hostname = host.value.toLowerCase();
    var prefix = '';
    if (hostname !== routingConfig.domainName &&
        hostname !== event.context.distributionDomainName) {
        var suffix = '.' + routingConfig.domainName;
        if (!hostname.endsWith(suffix)) return notFound();
        var label = hostname.slice(0, -suffix.length);
        if (!/^pr-[1-9][0-9]*$/.test(label)) return notFound();
        prefix = '/previews/' + label;
    }

    var uri = request.uri;
    if (!uri || uri.charAt(0) !== '/') return notFound();

    // Reject traversal and encoded separators before adding any preview prefix.
    var segments = uri.split('/');
    for (var i = 0; i < segments.length; i++) {
        var segment;
        try {
            segment = decodeURIComponent(segments[i]);
        } catch (error) {
            return notFound();
        }
        if (segment === '.' || segment === '..' ||
            segment.indexOf('/') !== -1 || segment.indexOf('\\') !== -1 ||
            /%[0-9a-f]{2}/i.test(segment)) {
            return notFound();
        }
    }

    // Vite assets stay as file requests, including missing/extensionless assets.
    // Other extensionless paths are React routes served by this site's index.html.
    var filename = segments[segments.length - 1];
    if (uri !== '/assets' && !uri.startsWith('/assets/') &&
        filename.indexOf('.') === -1) {
        uri = '/index.html';
    }

    request.uri = prefix + uri;
    return request;
}
