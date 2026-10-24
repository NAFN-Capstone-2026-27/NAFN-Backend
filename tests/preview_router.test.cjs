const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');

const source = readFileSync(join(
    __dirname, '../NAFNBackend/FrontendInfrastructure/preview_router.js'
), 'utf8').replace('__ROUTING_CONFIG__', JSON.stringify({
    domainName: 'dev.nafn.us'
}));
const context = {};
runInNewContext(source, context);

function event(host, uri) {
    return {
        context: { distributionDomainName: 'example.cloudfront.net' },
        request: {
            method: 'GET', uri,
            headers: { host: { value: host } },
            querystring: { search: { value: 'cats' } },
            cookies: { session: { value: 'test' } }
        }
    };
}

test('preview page routes use that PR index, including nested routes and refreshes', () => {
    for (const uri of ['/', '/about', '/about/', '/need-help', '/nested/page']) {
        assert.equal(context.handler(event('pr-12.dev.nafn.us', uri)).uri,
            '/previews/pr-12/index.html');
    }
});

test('assets and files keep their paths, including missing and extensionless assets', () => {
    for (const uri of ['/assets/app.js', '/assets/app.css', '/assets/missing',
        '/assets/logo.png', '/favicon.ico', '/index.html']) {
        assert.equal(context.handler(event('pr-12.dev.nafn.us', uri)).uri,
            '/previews/pr-12' + uri);
    }
});

test('different PRs map to different prefixes; hostname case is normalized', () => {
    assert.equal(context.handler(event('pr-13.dev.nafn.us', '/')).uri,
        '/previews/pr-13/index.html');
    assert.equal(context.handler(event('PR-12.DEV.NAFN.US', '/')).uri,
        '/previews/pr-12/index.html');
});

test('development and distribution URL requests pass through unchanged', () => {
    for (const host of ['dev.nafn.us', 'example.cloudfront.net']) {
        for (const uri of ['/', '/about', '/assets/app.js']) {
            const input = event(host, uri);
            assert.strictEqual(context.handler(input), input.request);
            assert.equal(input.request.uri, uri);
        }
    }
});

test('query strings, cookies, headers and methods survive rewriting', () => {
    const input = event('pr-12.dev.nafn.us', '/about');
    input.request.method = 'HEAD';
    const original = structuredClone(input.request);
    const result = context.handler(input);
    assert.deepEqual({ ...result, uri: original.uri }, original);
});

test('invalid or missing preview hosts return 404', () => {
    for (const host of ['preview.dev.nafn.us', 'foo.dev.nafn.us',
        'pr-0.dev.nafn.us', 'pr-01.dev.nafn.us',
        'pr-12.extra.dev.nafn.us', 'pr-12.dev.nafn.us.evil.test']) {
        assert.equal(context.handler(event(host, '/')).statusCode, 404);
    }
    const missingHost = event('pr-12.dev.nafn.us', '/');
    delete missingHost.request.headers.host;
    assert.equal(context.handler(missingHost).statusCode, 404);
});

test('malformed encodings and traversal cannot escape the PR prefix', () => {
    for (const uri of ['/../index.html', '/assets/../../index.html',
        '/%2e%2e/index.html', '/assets%2f..%2findex.html',
        '/%252e%252e/index.html', '/assets/..\\index.html', '/bad%ZZ.js']) {
        assert.equal(context.handler(event('pr-12.dev.nafn.us', uri)).statusCode, 404);
    }
});
