// Emite um token de sessão (30 dias) no ambiente grav, DENTRO do container do
// backend (leia-app): os usuários vivem no Mongo e o token é um JWT HS256
// assinado com SESSION_TOKEN_SECRET + ':' + tokenKey do usuário — o mesmo que
// src/usecases/auth/session_token.mjs. Roda com `node --env-file=.env` em
// /usr/src/app (DATABASE_URL e SESSION_TOKEN_SECRET vêm do .env do backend).
// Não imprime segredo no terminal: grava /tmp/leia_tk.json (600) no container.
import { createRequire } from 'node:module'
import { writeFileSync, chmodSync } from 'node:fs'

const require = createRequire('/usr/src/app/')
const jwt = require('jsonwebtoken')
const { MongoClient } = require('mongodb')

const EMAIL = (process.argv[2] || '').trim().toLowerCase()
const TTL = 60 * 60 * 24 * 30

function falha(msg, code) {
    console.log('ERRO: ' + msg)
    process.exit(code)
}

if (!EMAIL) falha('e-mail não informado', 1)
const secret = process.env.SESSION_TOKEN_SECRET
if (!secret) falha('SESSION_TOKEN_SECRET ausente no .env do backend', 2)
if (!process.env.DATABASE_URL) falha('DATABASE_URL ausente no .env do backend', 2)

const client = new MongoClient(process.env.DATABASE_URL, { serverSelectionTimeoutMS: 15000 })
try {
    await client.connect()
    const user = await client.db().collection('users').findOne(
        { emailLower: EMAIL }, { projection: { tokenKey: 1 } },
    )
    // _id é o id herdado do PocketBase (string), o mesmo do ownerId nos cards
    const id = user?._id != null ? String(user._id) : null
    if (!id) falha('usuário não encontrado nesse ambiente', 3)
    if (!user.tokenKey) falha('usuário sem tokenKey (nunca logou depois da migração?)', 4)

    const token = jwt.sign({ id, type: 'session' }, `${secret}:${user.tokenKey}`,
        { algorithm: 'HS256', expiresIn: TTL })
    const out = {
        token,
        userId: id,
        baseUrl: 'https://grav-lightsail.grav.digital/leia_prod',
        apiKey: 'rwf6HU',
        expiresAtMs: Date.now() + TTL * 1000,
    }
    const p = '/tmp/leia_tk.json'
    writeFileSync(p, JSON.stringify(out))
    chmodSync(p, 0o600)
    console.log(`OK: token emitido (${JSON.stringify(out).length} bytes)`)
} finally {
    await client.close()
}
