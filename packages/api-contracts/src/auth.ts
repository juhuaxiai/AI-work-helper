export interface RegisterRequest {
  readonly email: string;
  readonly displayName: string;
  readonly password: string;
}

export interface LoginRequest {
  readonly email: string;
  readonly password: string;
}

export interface AuthUser {
  readonly id: string;
  readonly email: string;
  readonly displayName: string;
  readonly createdAt: string;
  readonly organization?: OrganizationIdentity;
}

export interface OrganizationIdentity {
  readonly tenantKey: string;
  readonly openId: string;
  readonly departmentIds: readonly string[];
  readonly canDiagnose: boolean;
}

export interface AuthTokenResponse {
  readonly user: AuthUser;
  readonly accessToken: string;
  readonly tokenType: "bearer";
}

export interface LogoutResponse {
  readonly revoked: true;
}
