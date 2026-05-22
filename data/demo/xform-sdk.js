/**
 * XForm SDK — JavaScript/TypeScript
 * Build forms. Launch workflows.
 *
 * Usage :
 *   import { XFormClient } from './xform-sdk.js'
 *
 *   const xform = new XFormClient({ baseUrl: 'http://localhost:8000' })
 *   await xform.auth.setToken('Bearer eyJ...')
 *
 *   // Admin — créer un formulaire
 *   const { form } = await xform.forms.create({ title: 'Recrutement', fields: [...] })
 *
 *   // Public — charger et soumettre
 *   const { form } = await xform.public.getForm('recrutement')
 *   await xform.public.submit('recrutement', { nom: 'Alice', email: 'a@b.com' })
 */

// ─────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────

/**
 * @typedef {'text'|'email'|'number'|'date'|'textarea'|'select'|'radio'|'checkbox'|'file'|'signature'|'phone'|'url'|'hidden'|'section'|'divider'} FieldType
 */

/**
 * @typedef {'active'|'paused'|'archived'|'draft'} FormStatus
 */

/**
 * @typedef {Object} FieldValidation
 * @property {boolean} [required]
 * @property {number}  [min_length]
 * @property {number}  [max_length]
 * @property {number}  [min_value]
 * @property {number}  [max_value]
 * @property {string}  [pattern]
 * @property {string}  [custom_msg]
 */

/**
 * @typedef {Object} FormField
 * @property {string}          id
 * @property {FieldType}       type
 * @property {string}          label
 * @property {string}          name
 * @property {string}          [placeholder]
 * @property {string}          [help_text]
 * @property {any}             [default_value]
 * @property {Array<{label:string,value:string}>} [options]
 * @property {number}          order
 * @property {'full'|'half'|'third'} [width]
 * @property {FieldValidation} [validation]
 * @property {any}             [logic]
 */

/**
 * @typedef {Object} FormDefinition
 * @property {string}        id
 * @property {string}        title
 * @property {string}        [description]
 * @property {string}        slug
 * @property {string}        owner_id
 * @property {FormField[]}   fields
 * @property {any[]}         steps
 * @property {any}           settings
 * @property {any}           theme
 * @property {FormStatus}    status
 * @property {string[]}      tags
 */

// ─────────────────────────────────────────────────────────────
// Erreur custom
// ─────────────────────────────────────────────────────────────

export class XFormError extends Error {
    constructor(message, code, details) {
        super(message)
        this.name = 'XFormError'
        this.code = code || 'unknown'
        this.details = details || null
    }
}

// ─────────────────────────────────────────────────────────────
// HTTP client interne
// ─────────────────────────────────────────────────────────────

class HttpClient {
    constructor(baseUrl, pluginPrefix = '/plugin/xform') {
        this._base = baseUrl.replace(/\/$/, '')
        this._prefix = pluginPrefix
        this._token = null
        this._hooks = { beforeRequest: [], afterResponse: [] }
    }

    setToken(token) {
        this._token = token
    }

    /** Ajoute un hook avant chaque requête. fn(options) → options */
    onBeforeRequest(fn) {
        this._hooks.beforeRequest.push(fn)
        return this
    }

    /** Ajoute un hook après chaque réponse. fn(response) → void */
    onAfterResponse(fn) {
        this._hooks.afterResponse.push(fn)
        return this
    }

    _url(path) {
        return `${this._base}${this._prefix}${path}`
    }

    async _request(method, path, body, options = {}) {
        let reqOptions = {
            method,
            headers: {
                'Content-Type': 'application/json',
                ...(this._token ? { Authorization: this._token } : {}),
                ...(options.headers || {}),
            },
            ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
        }

        // Hooks before
        for (const hook of this._hooks.beforeRequest) {
            reqOptions = (await hook(reqOptions)) || reqOptions
        }

        const url = this._url(path)
        const response = await fetch(url, reqOptions)

        // Hooks after
        for (const hook of this._hooks.afterResponse) {
            await hook(response.clone())
        }

        if (options.raw) {
            return response
        }

        if (!response.ok) {
            let detail = `HTTP ${response.status}`
            try {
                const err = await response.json()
                detail = err.detail || err.msg || detail
            } catch { }
            throw new XFormError(detail, `http_${response.status}`)
        }

        const data = await response.json()
        if (data.status === 'error') {
            throw new XFormError(data.msg, data.code, data.errors)
        }
        return data
    }

    get(path, options) { return this._request('GET', path, undefined, options) }
    post(path, body, options) { return this._request('POST', path, body, options) }
    put(path, body, options) { return this._request('PUT', path, body, options) }
    del(path, options) { return this._request('DELETE', path, undefined, options) }
}

// ─────────────────────────────────────────────────────────────
// Module Auth
// ─────────────────────────────────────────────────────────────

class AuthModule {
    constructor(http) {
        this._http = http
    }

    /** Définit le token JWT (Bearer xxx) pour toutes les requêtes suivantes. */
    setToken(token) {
        this._http.setToken(
            token.startsWith('Bearer ') ? token : `Bearer ${token}`
        )
        return this
    }

    /** Supprime le token (déconnexion). */
    clearToken() {
        this._http.setToken(null)
        return this
    }
}

// ─────────────────────────────────────────────────────────────
// Module Forms (authentifié)
// ─────────────────────────────────────────────────────────────

class FormsModule {
    constructor(http) {
        this._http = http
    }

    /**
     * Crée un nouveau formulaire.
     * @param {{title:string, fields:FormField[], description?:string, settings?:any, theme?:any, tags?:string[]}} params
     * @returns {Promise<{form:FormDefinition}>}
     */
    create(params) {
        return this._http.post('/forms', params)
    }

    /**
     * Liste les formulaires de l'utilisateur connecté.
     * @param {{status?:string, limit?:number, offset?:number}} params
     * @returns {Promise<{forms:FormDefinition[]}>}
     */
    list(params = {}) {
        const qs = new URLSearchParams()
        if (params.status) qs.set('status', params.status)
        if (params.limit) qs.set('limit', String(params.limit))
        if (params.offset) qs.set('offset', String(params.offset))
        const q = qs.toString()
        return this._http.get(`/forms${q ? `?${q}` : ''}`)
    }

    /**
     * Récupère un formulaire par ID.
     * @param {string} formId
     * @returns {Promise<{form:FormDefinition}>}
     */
    get(formId) {
        return this._http.get(`/forms/${formId}`)
    }

    /**
     * Modifie un formulaire existant.
     * @param {string} formId
     * @param {Partial<FormDefinition>} updates
     * @returns {Promise<{form:FormDefinition}>}
     */
    update(formId, updates) {
        return this._http.put(`/forms/${formId}`, updates)
    }

    /**
     * Supprime un formulaire.
     * @param {string} formId
     * @returns {Promise<{message:string}>}
     */
    delete(formId) {
        return this._http.del(`/forms/${formId}`)
    }

    /**
     * Active un formulaire (change le statut à 'active').
     * @param {string} formId
     */
    activate(formId) {
        return this.update(formId, { status: 'active' })
    }

    /**
     * Met en pause un formulaire.
     * @param {string} formId
     */
    pause(formId) {
        return this.update(formId, { status: 'paused' })
    }

    /**
     * Archive un formulaire.
     * @param {string} formId
     */
    archive(formId) {
        return this.update(formId, { status: 'archived' })
    }
}

// ─────────────────────────────────────────────────────────────
// Module Submissions
// ─────────────────────────────────────────────────────────────

class SubmissionsModule {
    constructor(http) {
        this._http = http
    }

    /**
     * Liste les soumissions d'un formulaire.
     * @param {string} formId
     * @param {{status?:string, limit?:number, offset?:number}} params
     * @returns {Promise<{submissions:any[]}>}
     */
    list(formId, params = {}) {
        const qs = new URLSearchParams()
        if (params.status) qs.set('status', params.status)
        if (params.limit) qs.set('limit', String(params.limit))
        if (params.offset) qs.set('offset', String(params.offset))
        const q = qs.toString()
        return this._http.get(`/forms/${formId}/submissions${q ? `?${q}` : ''}`)
    }

    /**
     * Exporte les soumissions.
     * @param {string} formId
     * @param {'xlsx'|'csv'|'json'} format
     * @returns {Promise<Response>} — réponse brute (Blob)
     */
    async export(formId, format = 'xlsx') {
        const response = await this._http.get(
            `/forms/${formId}/export?format=${format}`,
            { raw: true }
        )
        if (!response.ok) throw new XFormError(`Export échoué: HTTP ${response.status}`)
        return response
    }

    /**
     * Télécharge l'export directement (déclenche le download navigateur).
     * @param {string} formId
     * @param {'xlsx'|'csv'|'json'} format
     * @param {string} [filename]
     */
    async download(formId, format = 'xlsx', filename) {
        const response = await this.export(formId, format)
        const blob = await response.blob()
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = filename || `export.${format}`
        a.click()
        URL.revokeObjectURL(url)
    }
}

// ─────────────────────────────────────────────────────────────
// Module Analytics
// ─────────────────────────────────────────────────────────────

class AnalyticsModule {
    constructor(http) {
        this._http = http
    }

    /**
     * Récupère les statistiques d'un formulaire.
     * @param {string} formId
     * @returns {Promise<{analytics:{total_views:number, total_submissions:number, completion_rate:number}}>}
     */
    get(formId) {
        return this._http.get(`/forms/${formId}/analytics`)
    }
}

// ─────────────────────────────────────────────────────────────
// Module Public (sans auth)
// ─────────────────────────────────────────────────────────────

class PublicModule {
    constructor(http) {
        this._http = http
    }

    /**
     * Charge la structure d'un formulaire par son slug.
     * @param {string} slug
     * @returns {Promise<{form:FormDefinition}>}
     */
    getForm(slug) {
        return this._http.get(`/public/${slug}`)
    }

    /**
     * Soumet une réponse JSON.
     * Pour les champs fichier, la valeur doit être le file_id
     * retourné par uploadFile().
     *
     * @param {string} slug
     * @param {Record<string,any>} data  — données (clé = field.name)
     * @param {{duration_sec?:number}} [meta]
     * @returns {Promise<{submission_id:string, message:string, redirect_url?:string}>}
     */
    submit(slug, data, meta = {}) {
        return this._http.post(`/public/${slug}/submit`, { data, meta })
    }

    /**
     * Upload un fichier pour un champ spécifique.
     * Retourne un file_id à utiliser dans submit().
     *
     * @param {string} slug          — slug du formulaire
     * @param {string} fieldName     — field.name du champ fichier
     * @param {File}   file          — objet File natif (input.files[0])
     * @param {(progress:number)=>void} [onProgress] — callback 0-100 (optionnel)
     * @returns {Promise<{file_id:string, original_name:string, size_bytes:number, mime_type:string}>}
     *
     * @example
     *   const { file_id } = await xform.public.uploadFile('recrutement', 'cv', fileInput.files[0])
     *   await xform.public.submit('recrutement', { nom: 'Alice', cv: file_id })
     */
    async uploadFile(slug, fieldName, file, onProgress) {
        const fd = new FormData()
        fd.append('field_name', fieldName)
        fd.append('file', file)

        // Si onProgress, utiliser XHR (fetch ne supporte pas la progression)
        if (onProgress && typeof XMLHttpRequest !== 'undefined') {
            return new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest()
                const url = this._http._url(`/public/${slug}/upload`)

                xhr.upload.addEventListener('progress', (e) => {
                    if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100))
                })
                xhr.addEventListener('load', () => {
                    try {
                        const data = JSON.parse(xhr.responseText)
                        if (xhr.status >= 400 || data.status === 'error') {
                            reject(new XFormError(data.detail || data.msg || `HTTP ${xhr.status}`, `http_${xhr.status}`))
                        } else {
                            onProgress && onProgress(100)
                            resolve(data)
                        }
                    } catch (e) {
                        reject(new XFormError('Réponse invalide du serveur', 'parse_error'))
                    }
                })
                xhr.addEventListener('error', () => reject(new XFormError('Erreur réseau', 'network_error')))

                const headers = {}
                if (this._http._token) headers['Authorization'] = this._http._token

                xhr.open('POST', url)
                for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v)
                xhr.send(fd)
            })
        }

        // fetch standard
        const url = this._http._url(`/public/${slug}/upload`)
        const headers = {}
        if (this._http._token) headers['Authorization'] = this._http._token

        const response = await fetch(url, { method: 'POST', body: fd, headers })
        if (!response.ok) {
            let detail = `HTTP ${response.status}`
            try { detail = (await response.json()).detail || detail } catch { }
            throw new XFormError(detail, `http_${response.status}`)
        }
        return response.json()
    }

    /**
     * Upload plusieurs fichiers en parallèle.
     *
     * @param {string} slug
     * @param {Record<string, File>} files — { fieldName: File, ... }
     * @param {(fieldName:string, progress:number)=>void} [onProgress]
     * @returns {Promise<Record<string, string>>} — { fieldName: file_id, ... }
     */
    async uploadFiles(slug, files, onProgress) {
        const entries = Object.entries(files)
        const results = await Promise.all(
            entries.map(([fieldName, file]) =>
                this.uploadFile(
                    slug,
                    fieldName,
                    file,
                    onProgress ? (p) => onProgress(fieldName, p) : undefined
                )
            )
        )
        return Object.fromEntries(entries.map(([name], i) => [name, results[i].file_id]))
    }

    /**
     * Soumet le formulaire en gérant automatiquement les fichiers.
     * Détecte les champs de type File, les uploade, puis soumet le tout.
     *
     * C'est la méthode recommandée quand les données peuvent contenir des fichiers.
     *
     * @param {string} slug
     * @param {Record<string, any|File>} data  — peut mélanger texte et File
     * @param {{duration_sec?:number}} [meta]
     * @param {(fieldName:string, progress:number)=>void} [onProgress]
     * @returns {Promise<{submission_id:string, message:string, redirect_url?:string}>}
     *
     * @example
     *   await xform.public.submitWithFiles('recrutement', {
     *     nom:   'Alice',
     *     email: 'alice@example.com',
     *     cv:    cvInput.files[0],       // ← File natif, géré automatiquement
     *   })
     */
    async submitWithFiles(slug, data, meta = {}, onProgress) {
        const fileEntries = Object.entries(data).filter(([, v]) => v instanceof File)
        const textData = Object.fromEntries(
            Object.entries(data).filter(([, v]) => !(v instanceof File))
        )

        // Upload les fichiers en parallèle
        if (fileEntries.length > 0) {
            const fileMap = Object.fromEntries(fileEntries)
            const fileIds = await this.uploadFiles(slug, fileMap, onProgress)
            Object.assign(textData, fileIds)
        }

        return this.submit(slug, textData, meta)
    }

    /**
     * Soumission tout-en-un via multipart (un seul appel réseau).
     * Alternative à submitWithFiles() — moins de requêtes mais pas de progression.
     *
     * @param {string} slug
     * @param {Record<string, any|File>} data
     * @param {{duration_sec?:number}} [meta]
     */
    async submitMultipart(slug, data, meta = {}) {
        const fd = new FormData()
        for (const [key, value] of Object.entries(data)) {
            if (value instanceof File) {
                fd.append(key, value, value.name)
            } else if (Array.isArray(value)) {
                fd.append(key, JSON.stringify(value))
            } else if (value !== null && value !== undefined) {
                fd.append(key, String(value))
            }
        }
        if (Object.keys(meta).length > 0) {
            fd.append('__meta__', JSON.stringify(meta))
        }

        const url = this._http._url(`/public/${slug}/submit-form`)
        const headers = {}
        if (this._http._token) headers['Authorization'] = this._http._token

        const response = await fetch(url, { method: 'POST', body: fd, headers })
        if (!response.ok) {
            let detail = `HTTP ${response.status}`
            try { detail = (await response.json()).detail || detail } catch { }
            throw new XFormError(detail, `http_${response.status}`)
        }
        const result = await response.json()
        if (result.status === 'error') throw new XFormError(result.msg, result.code)
        return result
    }
}

// ─────────────────────────────────────────────────────────────
// Builder de formulaire — fluent API
// ─────────────────────────────────────────────────────────────

export class FormBuilder {
    constructor(title, ownerOrClient) {
        this._form = {
            title,
            description: null,
            fields: [],
            steps: [],
            settings: {},
            theme: {},
            tags: [],
        }
        this._owner = typeof ownerOrClient === 'string' ? ownerOrClient : null
        this._client = typeof ownerOrClient === 'object' ? ownerOrClient : null
        this._fieldOrder = 0
    }

    description(text) {
        this._form.description = text
        return this
    }

    tag(...tags) {
        this._form.tags.push(...tags)
        return this
    }

    theme(themeConfig) {
        this._form.theme = { ...this._form.theme, ...themeConfig }
        return this
    }

    settings(settingsConfig) {
        this._form.settings = { ...this._form.settings, ...settingsConfig }
        return this
    }

    // ── Raccourcis champs ───────────────────────────────────

    /** Ajoute un champ de type texte. */
    text(name, label, options = {}) {
        return this._addField('text', name, label, options)
    }

    /** Ajoute un champ email. */
    email(name = 'email', label = 'Email', options = {}) {
        return this._addField('email', name, label, options)
    }

    /** Ajoute un champ numérique. */
    number(name, label, options = {}) {
        return this._addField('number', name, label, options)
    }

    /** Ajoute un champ date. */
    date(name, label, options = {}) {
        return this._addField('date', name, label, options)
    }

    /** Ajoute un textarea. */
    textarea(name, label, options = {}) {
        return this._addField('textarea', name, label, options)
    }

    /** Ajoute un select (liste déroulante). */
    select(name, label, choices, options = {}) {
        return this._addField('select', name, label, {
            ...options,
            options: choices.map(c =>
                typeof c === 'string' ? { label: c, value: c } : c
            ),
        })
    }

    /** Ajoute des radios. */
    radio(name, label, choices, options = {}) {
        return this._addField('radio', name, label, {
            ...options,
            options: choices.map(c =>
                typeof c === 'string' ? { label: c, value: c } : c
            ),
        })
    }

    /** Ajoute des checkboxes. */
    checkbox(name, label, choices, options = {}) {
        return this._addField('checkbox', name, label, {
            ...options,
            options: choices.map(c =>
                typeof c === 'string' ? { label: c, value: c } : c
            ),
        })
    }

    /** Ajoute un champ téléphone. */
    phone(name = 'telephone', label = 'Téléphone', options = {}) {
        return this._addField('phone', name, label, options)
    }

    /** Ajoute un upload de fichier. */
    file(name, label, options = {}) {
        return this._addField('file', name, label, options)
    }

    /** Ajoute une signature. */
    signature(name = 'signature', label = 'Signature', options = {}) {
        return this._addField('signature', name, label, options)
    }

    /** Ajoute une section (layout). */
    section(title) {
        return this._addField('section', `section_${this._fieldOrder}`, title, {})
    }

    /** Rend le dernier champ ajouté obligatoire. */
    required(message) {
        const last = this._form.fields[this._form.fields.length - 1]
        if (last) {
            last.validation = last.validation || {}
            last.validation.required = true
            if (message) last.validation.custom_msg = message
        }
        return this
    }

    /** Ajoute une logique conditionnelle au dernier champ. */
    showIf(fieldName, operator, value) {
        const last = this._form.fields[this._form.fields.length - 1]
        if (last) {
            last.logic = {
                rules: [{ field_id: fieldName, operator, value, action: 'show' }],
                match_all: true,
            }
        }
        return this
    }

    /** Déclenche un workflow XFlow à la soumission. */
    workflow(workflowId) {
        this._form.settings.workflow_id = workflowId
        return this
    }

    /** Active l'email de confirmation. */
    confirmationEmail(enabled = true) {
        this._form.settings.confirmation_email = enabled
        return this
    }

    /** Message affiché après soumission. */
    confirmationMessage(message) {
        this._form.settings.confirmation_message = message
        return this
    }

    /** Redirige vers une URL après soumission. */
    redirectTo(url) {
        this._form.settings.redirect_url = url
        return this
    }

    /** Retourne l'objet formulaire construit. */
    build() {
        if (this._owner) {
            return { ...this._form, owner_id: this._owner }
        }
        return { ...this._form }
    }

    /**
     * Sauvegarde le formulaire via le client XForm.
     * Nécessite que le FormBuilder ait été instancié avec un XFormClient.
     */
    async save() {
        if (!this._client) {
            throw new XFormError(
                'Impossible de sauvegarder : aucun XFormClient fourni au FormBuilder.',
                'no_client'
            )
        }
        const payload = this.build()
        return this._client.forms.create(payload)
    }

    // ── Interne ─────────────────────────────────────────────

    _addField(type, name, label, options) {
        const field = {
            type,
            name,
            label,
            order: this._fieldOrder++,
            placeholder: options.placeholder || null,
            help_text: options.help_text || null,
            options: options.options || [],
            width: options.width || 'full',
            validation: options.validation || {},
            logic: options.logic || null,
        }
        if (options.required) {
            field.validation.required = true
        }
        this._form.fields.push(field)
        return this
    }
}

// ─────────────────────────────────────────────────────────────
// Client principal
// ─────────────────────────────────────────────────────────────

export class XFormClient {
    /**
     * @param {{
     *   baseUrl: string,
     *   token?: string,
     *   pluginPrefix?: string
     * }} config
     */
    constructor(config = {}) {
        const {
            baseUrl = 'http://localhost:8000',
            token,
            pluginPrefix = '/app/xform',
        } = config

        this._http = new HttpClient(baseUrl, pluginPrefix)

        this.auth = new AuthModule(this._http)
        this.forms = new FormsModule(this._http)
        this.submissions = new SubmissionsModule(this._http)
        this.analytics = new AnalyticsModule(this._http)
        this.public = new PublicModule(this._http)

        if (token) this.auth.setToken(token)
    }

    /**
     * Crée un FormBuilder lié à ce client.
     * @param {string} title
     * @returns {FormBuilder}
     */
    builder(title) {
        return new FormBuilder(title, this)
    }

    /** Intercepteur HTTP. */
    onBeforeRequest(fn) {
        this._http.onBeforeRequest(fn)
        return this
    }

    onAfterResponse(fn) {
        this._http.onAfterResponse(fn)
        return this
    }
}

// ─────────────────────────────────────────────────────────────
// Helper — rendu dynamique d'un formulaire en HTML vanilla
// ─────────────────────────────────────────────────────────────

export class XFormRenderer {
    /**
     * Rend un formulaire dans un conteneur DOM.
     *
     * @param {HTMLElement} container
     * @param {FormDefinition} form
     * @param {{
     *   onSubmit?: (data:Record<string,any>, meta:any) => Promise<void>,
     *   xformClient?: XFormClient,
     *   showLabels?: boolean,
     * }} options
     */
    constructor(container, form, options = {}) {
        this._container = container
        this._form = form
        this._options = options
        this._data = {}
        this._startTime = Date.now()
    }

    render() {
        const theme = this._form.theme || {}
        this._container.innerHTML = ''

        // Inject CSS variables
        this._container.style.setProperty('--xform-primary', theme.primary_color || '#3B82F6')
        this._container.style.setProperty('--xform-bg', theme.bg_color || '#fff')
        this._container.style.setProperty('--xform-text', theme.text_color || '#111827')
        this._container.style.setProperty('--xform-radius', theme.border_radius || '8px')

        // Header
        const header = document.createElement('div')
        header.className = 'xform-header'
        header.innerHTML = `
      <h2 class="xform-title">${this._form.title}</h2>
      ${this._form.description ? `<p class="xform-description">${this._form.description}</p>` : ''}
    `
        this._container.appendChild(header)

        // Fields
        const fieldsDiv = document.createElement('div')
        fieldsDiv.className = 'xform-fields'

        const sorted = [...(this._form.fields || [])].sort((a, b) => a.order - b.order)
        for (const field of sorted) {
            const wrapper = this._renderField(field)
            if (wrapper) fieldsDiv.appendChild(wrapper)
        }
        this._container.appendChild(fieldsDiv)

        // Submit button
        const submitBtn = document.createElement('button')
        submitBtn.className = 'xform-submit'
        submitBtn.textContent = 'Envoyer'
        submitBtn.type = 'button'
        submitBtn.addEventListener('click', () => this._handleSubmit())
        this._container.appendChild(submitBtn)

        // Erreurs
        const errorsDiv = document.createElement('div')
        errorsDiv.className = 'xform-errors'
        errorsDiv.style.display = 'none'
        this._container.appendChild(errorsDiv)
        this._errorsDiv = errorsDiv

        return this
    }

    _renderField(field) {
        if (['section', 'divider', 'hidden'].includes(field.type)) {
            const el = document.createElement(field.type === 'section' ? 'h3' : 'hr')
            if (field.type === 'section') {
                el.className = 'xform-section'
                el.textContent = field.label
            }
            return el
        }

        const wrapper = document.createElement('div')
        wrapper.className = `xform-field xform-field--${field.width || 'full'}`
        wrapper.dataset.fieldId = field.id

        const label = document.createElement('label')
        label.htmlFor = `xform_${field.name}`
        label.className = 'xform-label'
        label.innerHTML = `${field.label}${field.validation?.required ? ' <span class="xform-required">*</span>' : ''}`

        let input

        if (['select'].includes(field.type)) {
            input = document.createElement('select')
            const placeholder = document.createElement('option')
            placeholder.value = ''
            placeholder.textContent = field.placeholder || 'Sélectionner...'
            input.appendChild(placeholder)
            for (const opt of (field.options || [])) {
                const option = document.createElement('option')
                option.value = opt.value
                option.textContent = opt.label
                input.appendChild(option)
            }
        } else if (field.type === 'radio') {
            input = document.createElement('div')
            input.className = 'xform-radio-group'
            for (const opt of (field.options || [])) {
                const radioWrapper = document.createElement('label')
                radioWrapper.className = 'xform-radio-label'
                const radio = document.createElement('input')
                radio.type = 'radio'
                radio.name = field.name
                radio.value = opt.value
                radio.addEventListener('change', () => { this._data[field.name] = radio.value })
                radioWrapper.appendChild(radio)
                radioWrapper.appendChild(document.createTextNode(` ${opt.label}`))
                input.appendChild(radioWrapper)
            }
        } else if (field.type === 'checkbox') {
            input = document.createElement('div')
            input.className = 'xform-checkbox-group'
            for (const opt of (field.options || [])) {
                const cbWrapper = document.createElement('label')
                cbWrapper.className = 'xform-checkbox-label'
                const cb = document.createElement('input')
                cb.type = 'checkbox'
                cb.name = field.name
                cb.value = opt.value
                cb.addEventListener('change', () => {
                    const checked = input.querySelectorAll('input:checked')
                    this._data[field.name] = Array.from(checked).map(c => c.value)
                })
                cbWrapper.appendChild(cb)
                cbWrapper.appendChild(document.createTextNode(` ${opt.label}`))
                input.appendChild(cbWrapper)
            }
        } else if (field.type === 'textarea') {
            input = document.createElement('textarea')
            input.rows = 4
            input.placeholder = field.placeholder || ''
            input.addEventListener('input', () => { this._data[field.name] = input.value })
        } else if (field.type === 'file') {
            // Champ fichier avec preview + progression
            const fileWrapper = document.createElement('div')
            fileWrapper.className = 'xform-file-wrapper'
            input = document.createElement('input')
            input.type = 'file'
            input.className = 'xform-input'
            input.id = `xform_${field.name}`
            if (field.accept) input.accept = field.accept
            const progressWrap = document.createElement('div')
            progressWrap.className = 'xform-file-progress'
            progressWrap.style.display = 'none'
            progressWrap.innerHTML = `<div class="xform-progress-bar"><div class="xform-progress-fill" style="width:0%"></div></div><span class="xform-progress-text">0%</span>`
            const statusLine = document.createElement('p')
            statusLine.className = 'xform-file-status'
            input.addEventListener('change', () => {
                const file = input.files[0]
                if (!file) { statusLine.textContent = ''; return }
                this._data[field.name] = file
                statusLine.textContent = '📎 ' + file.name + ' (' + (file.size / 1024).toFixed(1) + ' KB)'
                statusLine.style.color = '#16A34A'
            })
            fileWrapper.appendChild(input)
            fileWrapper.appendChild(progressWrap)
            fileWrapper.appendChild(statusLine)
            if (!this._fileProgressBars) this._fileProgressBars = {}
            if (!this._fileStatusLines) this._fileStatusLines = {}
            this._fileProgressBars[field.name] = { wrap: progressWrap, fill: progressWrap.querySelector('.xform-progress-fill'), text: progressWrap.querySelector('.xform-progress-text') }
            this._fileStatusLines[field.name] = statusLine
            wrapper.appendChild(label)
            wrapper.appendChild(fileWrapper)
            if (field.help_text) {
                const hint = document.createElement('p')
                hint.className = 'xform-hint'
                hint.textContent = field.help_text
                wrapper.appendChild(hint)
            }
            return wrapper
        } else {
            input = document.createElement('input')
            const typeMap = { email: 'email', number: 'number', date: 'date', phone: 'tel', url: 'url' }
            input.type = typeMap[field.type] || 'text'
            input.placeholder = field.placeholder || ''
            if (field.default_value) input.value = field.default_value
            input.addEventListener('input', () => { this._data[field.name] = input.value })
        }

        if (input.classList !== undefined) input.className = 'xform-input'
        if (input.id !== undefined) input.id = `xform_${field.name}`

        if (field.help_text) {
            const hint = document.createElement('p')
            hint.className = 'xform-hint'
            hint.textContent = field.help_text
            wrapper.appendChild(label)
            wrapper.appendChild(input)
            wrapper.appendChild(hint)
        } else {
            wrapper.appendChild(label)
            wrapper.appendChild(input)
        }

        return wrapper
    }

    async _handleSubmit() {
        this._errorsDiv.style.display = 'none'
        this._errorsDiv.innerHTML = ''

        const duration = Math.round((Date.now() - this._startTime) / 1000)
        const meta = { duration_sec: duration }

        if (this._options.onSubmit) {
            await this._options.onSubmit(this._data, meta)
            return
        }

        if (this._options.xformClient && this._form.slug) {
            const submitBtn = this._container.querySelector('.xform-submit')
            if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Envoi...' }

            try {
                // submitWithFiles gère automatiquement l'upload des champs File
                const result = await this._options.xformClient.public.submitWithFiles(
                    this._form.slug,
                    this._data,
                    meta,
                    // Callback progression par champ fichier
                    (fieldName, progress) => {
                        const bars = this._fileProgressBars && this._fileProgressBars[fieldName]
                        const status = this._fileStatusLines && this._fileStatusLines[fieldName]
                        if (bars) {
                            bars.wrap.style.display = 'flex'
                            bars.fill.style.width = progress + '%'
                            bars.text.textContent = progress + '%'
                        }
                        if (status && progress === 100) {
                            status.textContent = '✅ Fichier envoyé'
                            status.style.color = '#16A34A'
                        }
                    }
                )
                if (result.redirect_url) {
                    window.location.href = result.redirect_url
                } else {
                    this._container.innerHTML = `
            <div class="xform-success">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2">
                <polyline points="20 6 9 17 4 12"/>
              </svg>
              <p>${result.message || 'Merci pour votre soumission !'}</p>
            </div>
          `
                }
            } catch (err) {
                if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Envoyer' }
                this._showErrors(err)
            }
        }
    }

    _showErrors(err) {
        this._errorsDiv.style.display = 'block'
        if (err.details && Array.isArray(err.details)) {
            this._errorsDiv.innerHTML = err.details
                .map(e => `<p class="xform-error-item">⚠ ${e.message}</p>`)
                .join('')
        } else {
            this._errorsDiv.innerHTML = `<p class="xform-error-item">⚠ ${err.message}</p>`
        }
    }
}

// ─────────────────────────────────────────────────────────────
// CSS embarqué (inject dans <head>)
// ─────────────────────────────────────────────────────────────

export function injectXFormStyles() {
    if (document.getElementById('xform-styles')) return
    const style = document.createElement('style')
    style.id = 'xform-styles'
    style.textContent = `
    :root {
      --xform-primary: #3B82F6;
      --xform-bg: #fff;
      --xform-text: #111827;
      --xform-radius: 8px;
      --xform-border: #E5E7EB;
      --xform-muted: #6B7280;
    }
    .xform-header { margin-bottom: 2rem; }
    .xform-title { font-size: 1.5rem; font-weight: 700; color: var(--xform-text); margin: 0 0 .5rem; }
    .xform-description { color: var(--xform-muted); margin: 0; }
    .xform-fields { display: flex; flex-wrap: wrap; gap: 1.25rem; }
    .xform-field { display: flex; flex-direction: column; gap: .4rem; }
    .xform-field--full  { width: 100%; }
    .xform-field--half  { width: calc(50% - .625rem); }
    .xform-field--third { width: calc(33.33% - .83rem); }
    .xform-label { font-size: .875rem; font-weight: 500; color: var(--xform-text); }
    .xform-required { color: #EF4444; }
    .xform-input, select.xform-input, textarea.xform-input {
      width: 100%; padding: .6rem .875rem; border: 1.5px solid var(--xform-border);
      border-radius: var(--xform-radius); font-size: 1rem; color: var(--xform-text);
      background: var(--xform-bg); box-sizing: border-box; outline: none;
      transition: border-color .15s;
    }
    .xform-input:focus { border-color: var(--xform-primary); box-shadow: 0 0 0 3px color-mix(in srgb, var(--xform-primary) 15%, transparent); }
    .xform-hint { font-size: .8rem; color: var(--xform-muted); margin: 0; }
    .xform-radio-group, .xform-checkbox-group { display: flex; flex-direction: column; gap: .5rem; }
    .xform-radio-label, .xform-checkbox-label { display: flex; align-items: center; gap: .5rem; cursor: pointer; font-size: .95rem; }
    .xform-section { font-size: 1.1rem; font-weight: 600; color: var(--xform-text); margin: .5rem 0 0; width: 100%; border-bottom: 2px solid var(--xform-border); padding-bottom: .5rem; }
    .xform-submit {
      margin-top: 1.5rem; padding: .75rem 2rem; background: var(--xform-primary);
      color: #fff; border: none; border-radius: var(--xform-radius); font-size: 1rem;
      font-weight: 600; cursor: pointer; transition: opacity .15s;
    }
    .xform-submit:hover { opacity: .9; }
    .xform-errors { margin-top: 1rem; }
    .xform-error-item { color: #EF4444; font-size: .9rem; margin: .25rem 0; }
    .xform-file-wrapper { display: flex; flex-direction: column; gap: .4rem; }
    .xform-file-status { font-size: .85rem; margin: 0; }
    .xform-file-progress { display: flex; align-items: center; gap: .5rem; }
    .xform-progress-bar { flex: 1; height: 6px; background: #E5E7EB; border-radius: 3px; overflow: hidden; }
    .xform-progress-fill { height: 100%; background: var(--xform-primary); border-radius: 3px; transition: width .2s; }
    .xform-progress-text { font-size: .75rem; color: var(--xform-muted); min-width: 2.5rem; }
    .xform-success { text-align: center; padding: 2rem; color: var(--xform-text); }
    .xform-success p { font-size: 1.1rem; margin-top: .75rem; }
    @media (max-width: 640px) {
      .xform-field--half, .xform-field--third { width: 100%; }
    }
  `
    document.head.appendChild(style)
}

// ─────────────────────────────────────────────────────────────
// Export par défaut
// ─────────────────────────────────────────────────────────────

export default XFormClient