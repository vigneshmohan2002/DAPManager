(() => {
    'use strict';

    const METHODS = ['get', 'post', 'put', 'patch', 'delete'];
    const state = { spec: null, operations: [] };
    const content = document.querySelector('#content');
    const nav = document.querySelector('#endpoint-nav');
    const filter = document.querySelector('#filter');
    const template = document.querySelector('#operation-template');

    function appendInlineMarkdown(element, text) {
        const pattern = /(``[^`]+``|`[^`]+`|\*\*[^*]+\*\*)/g;
        let offset = 0;
        for (const match of text.matchAll(pattern)) {
            if (match.index > offset) {
                element.append(document.createTextNode(text.slice(offset, match.index)));
            }
            const token = match[0];
            const child = token.startsWith('**')
                ? document.createElement('strong')
                : document.createElement('code');
            if (token.startsWith('**')) {
                appendInlineMarkdown(child, token.slice(2, -2));
            } else {
                child.textContent = token.startsWith('``')
                    ? token.slice(2, -2)
                    : token.slice(1, -1);
            }
            element.append(child);
            offset = match.index + token.length;
        }
        if (offset < text.length) {
            element.append(document.createTextNode(text.slice(offset)));
        }
    }

    function renderMarkdownBlocks(container, markdown) {
        let list = null;
        let paragraphLines = [];
        const flushParagraph = () => {
            if (!paragraphLines.length) return;
            const paragraph = document.createElement('p');
            appendInlineMarkdown(paragraph, paragraphLines.join(' '));
            container.append(paragraph);
            paragraphLines = [];
        };
        const closeBlocks = () => {
            flushParagraph();
            list = null;
        };
        for (const raw of String(markdown || '').split(/\r?\n/)) {
            const line = raw.trim();
            if (!line) {
                closeBlocks();
                continue;
            }
            const heading = /^(#{2,3})\s+(.+)$/.exec(line);
            if (heading) {
                closeBlocks();
                const element = document.createElement(heading[1].length === 2 ? 'h3' : 'h4');
                appendInlineMarkdown(element, heading[2]);
                container.append(element);
                continue;
            }
            const ordered = /^\d+\.\s+(.+)$/.exec(line);
            if (ordered) {
                flushParagraph();
                if (!list) {
                    list = document.createElement('ol');
                    container.append(list);
                }
                const item = document.createElement('li');
                appendInlineMarkdown(item, ordered[1]);
                list.append(item);
                continue;
            }
            list = null;
            paragraphLines.push(line);
        }
        closeBlocks();
    }

    function resolveSchema(schema) {
        if (!schema?.$ref) return schema;
        const prefix = '#/components/schemas/';
        if (!schema.$ref.startsWith(prefix)) return schema;
        return state.spec.components?.schemas?.[schema.$ref.slice(prefix.length)] || schema;
    }

    function schemaExample(schema) {
        schema = resolveSchema(schema);
        if (!schema) return null;
        if (schema.example !== undefined) return schema.example;
        if (schema.default !== undefined) return schema.default;
        if (schema.enum?.length) return schema.enum[0];
        if (schema.type === 'array') return [schemaExample(schema.items)];
        if (schema.type === 'object' || schema.properties) {
            const value = {};
            for (const [key, property] of Object.entries(schema.properties || {})) {
                value[key] = schemaExample(property);
            }
            return value;
        }
        if (schema.type === 'boolean') return false;
        if (schema.type === 'integer' || schema.type === 'number') return 0;
        return '';
    }

    function requestContent(operation) {
        const contentMap = operation.requestBody?.content || {};
        if (contentMap['application/json']) {
            return { type: 'json', schema: resolveSchema(contentMap['application/json'].schema) };
        }
        if (contentMap['multipart/form-data']) {
            return { type: 'multipart', schema: resolveSchema(contentMap['multipart/form-data'].schema) };
        }
        return null;
    }

    function operationId(method, path, operation) {
        return operation.operationId || `${method}-${path}`.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '');
    }

    function render() {
        const query = filter.value.trim().toLowerCase();
        content.replaceChildren();
        nav.replaceChildren();

        const intro = document.createElement('div');
        intro.className = 'intro';
        const title = document.createElement('h2');
        title.textContent = state.spec.info?.title || 'API';
        intro.append(title);
        renderMarkdownBlocks(intro, state.spec.info?.description || '');
        content.append(intro);

        const visible = state.operations.filter(item => item.search.includes(query));
        const groups = new Map();
        visible.forEach(item => {
            const tag = item.operation.tags?.[0] || 'Other';
            if (!groups.has(tag)) groups.set(tag, []);
            groups.get(tag).push(item);
        });

        for (const [tag, operations] of groups) {
            const group = document.createElement('section');
            group.className = 'tag-group';
            group.id = `tag-${tag.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
            const heading = document.createElement('h2');
            heading.textContent = tag;
            group.append(heading);

            const navLink = document.createElement('a');
            navLink.href = `#${group.id}`;
            navLink.textContent = `${tag} (${operations.length})`;
            nav.append(navLink);

            operations.forEach(item => group.append(renderOperation(item)));
            content.append(group);
        }

        if (!visible.length) {
            const empty = document.createElement('div');
            empty.className = 'empty';
            empty.textContent = 'No endpoints match that filter.';
            content.append(empty);
        }
    }

    function renderOperation(item) {
        const node = template.content.firstElementChild.cloneNode(true);
        const heading = node.querySelector('.operation-heading');
        const body = node.querySelector('.operation-body');
        const method = node.querySelector('.method');
        const id = operationId(item.method, item.path, item.operation);
        node.id = id;
        method.textContent = item.method.toUpperCase();
        method.classList.add(item.method);
        node.querySelector('.path').textContent = item.path;
        node.querySelector('.summary').textContent = item.operation.summary || '';
        const description = node.querySelector('.description');
        description.textContent = '';
        appendInlineMarkdown(
            description,
            item.operation.description || item.operation.summary || 'No description supplied.',
        );
        heading.addEventListener('click', () => {
            const open = heading.getAttribute('aria-expanded') !== 'true';
            heading.setAttribute('aria-expanded', String(open));
            body.hidden = !open;
        });

        const parameterBox = node.querySelector('.parameters');
        const parameters = [...(item.pathItem.parameters || []), ...(item.operation.parameters || [])];
        parameters.forEach(parameter => {
            const label = document.createElement('label');
            label.className = 'parameter';
            label.textContent = parameter.name;
            const hint = document.createElement('small');
            hint.textContent = ` (${parameter.in}${parameter.required ? ', required' : ''})`;
            label.append(hint);
            const input = document.createElement('input');
            input.name = `parameter-${parameter.in}-${parameter.name}`;
            input.dataset.location = parameter.in;
            input.dataset.parameter = parameter.name;
            input.required = Boolean(parameter.required);
            const example = parameter.example ?? parameter.schema?.example ?? parameter.schema?.default;
            if (example !== undefined) input.value = String(example);
            label.append(input);
            parameterBox.append(label);
        });

        const request = requestContent(item.operation);
        const bodyField = node.querySelector('.body-field');
        if (request?.type === 'json') {
            bodyField.hidden = false;
            const example = schemaExample(request.schema);
            bodyField.querySelector('textarea').value = JSON.stringify(example, null, 2);
        } else if (request?.type === 'multipart') {
            node.querySelector('.file-field').hidden = false;
        }

        node.querySelector('.try-form').addEventListener('submit', event => execute(event, node, item));
        return node;
    }

    async function execute(event, node, item) {
        event.preventDefault();
        const submit = node.querySelector('.execute');
        const response = node.querySelector('.response');
        const responseMeta = node.querySelector('.response-meta');
        const output = response.querySelector('pre');
        const query = new URLSearchParams();
        let path = item.path;

        node.querySelectorAll('[data-parameter]').forEach(input => {
            if (!input.value) return;
            const name = input.dataset.parameter;
            if (input.dataset.location === 'path') {
                path = path.replace(`{${name}}`, encodeURIComponent(input.value));
            } else if (input.dataset.location === 'query') {
                query.set(name, input.value);
            }
        });
        const url = `${path}${query.size ? `?${query}` : ''}`;
        node.querySelector('.request-url').textContent = `${item.method.toUpperCase()} ${url}`;

        const headers = { Accept: 'application/json' };
        const token = node.querySelector('[name="token"]').value.trim();
        if (token) headers.Authorization = `Bearer ${token}`;
        const init = { method: item.method.toUpperCase(), headers };
        const bodyField = node.querySelector('.body-field textarea');
        const fileField = node.querySelector('.file-field');
        if (!bodyField.closest('label').hidden && bodyField.value.trim()) {
            try {
                JSON.parse(bodyField.value);
            } catch (error) {
                response.hidden = false;
                responseMeta.className = 'response-meta error';
                responseMeta.textContent = `Invalid request JSON: ${error.message}`;
                output.textContent = bodyField.value;
                return;
            }
            headers['Content-Type'] = 'application/json';
            init.body = bodyField.value;
        } else if (!fileField.hidden) {
            const file = fileField.querySelector('input').files[0];
            if (!file) {
                response.hidden = false;
                responseMeta.className = 'response-meta error';
                responseMeta.textContent = 'Choose a file first.';
                output.textContent = '';
                return;
            }
            const form = new FormData();
            form.append('file', file);
            init.body = form;
        }

        submit.disabled = true;
        response.hidden = false;
        responseMeta.className = 'response-meta';
        responseMeta.textContent = 'Requesting…';
        output.textContent = '';
        const started = performance.now();
        try {
            const result = await fetch(url, init);
            const text = await result.text();
            const elapsed = Math.round(performance.now() - started);
            responseMeta.className = `response-meta ${result.ok ? 'ok' : 'error'}`;
            responseMeta.textContent = `${result.status} ${result.statusText} · ${elapsed} ms`;
            try {
                output.textContent = JSON.stringify(JSON.parse(text), null, 2);
            } catch {
                output.textContent = text || '(empty response)';
            }
        } catch (error) {
            responseMeta.className = 'response-meta error';
            responseMeta.textContent = 'Network error';
            output.textContent = String(error);
        } finally {
            submit.disabled = false;
        }
    }

    async function boot() {
        try {
            const response = await fetch('/api/openapi.json');
            if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
            state.spec = await response.json();
            for (const [path, pathItem] of Object.entries(state.spec.paths || {})) {
                for (const method of METHODS) {
                    const operation = pathItem[method];
                    if (!operation) continue;
                    state.operations.push({
                        path, pathItem, method, operation,
                        search: `${method} ${path} ${operation.summary || ''} ${(operation.tags || []).join(' ')}`.toLowerCase(),
                    });
                }
            }
            filter.addEventListener('input', render);
            render();
        } catch (error) {
            const message = document.createElement('div');
            message.className = 'empty';
            message.textContent = `Could not load the OpenAPI specification: ${String(error)}`;
            content.replaceChildren(message);
        }
    }

    boot();
})();
