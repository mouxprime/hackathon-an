// Compte utilisateur affiché à droite du header global (charte « Hémicycle ») : pastille d'initiales teal + e-mail (visible à partir de `lg`).
//
// TODO auth : l'e-mail est pour l'instant une valeur de maquette. Le brancher sur
// la vraie identité (JWT / SSO) quand l'authentification sera disponible.

const USER_EMAIL = 'citoyen@exemple.fr'

// Initiales dérivées de la partie locale de l'e-mail (« maquette » → « MA »).
function initials(email: string): string {
  const local = email.split('@')[0] || email
  const parts = local.split(/[._-]+/).filter(Boolean)
  const letters = parts.length >= 2 ? parts[0][0] + parts[1][0] : local.slice(0, 2)
  return letters.toUpperCase()
}

export function UserAccount() {
  return (
    <div className="flex items-center gap-2" title={USER_EMAIL}>
      <span className="flex h-7 w-7 items-center justify-center rounded-full bg-teal-500 text-[11px] font-bold text-white">
        {initials(USER_EMAIL)}
      </span>
      <span className="mono hidden text-xs text-navy-200 lg:block">{USER_EMAIL}</span>
    </div>
  )
}
