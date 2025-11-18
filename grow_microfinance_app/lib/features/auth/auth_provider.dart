import 'package:flutter/material.dart';
import '../../core/api_client.dart';
import '../../core/auth_storage.dart';

class AuthProvider extends ChangeNotifier {
  final AuthStorage _storage = AuthStorage();
  bool loading = false;
  String? token;
  String? role;

  Future<bool> tryAutoLogin() async {
    final creds = await _storage.loadCredentials();
    token = creds['token'];
    role = creds['role'];
    notifyListeners();
    return token != null && role != null;
  }

  Future<String?> login(String email, String password) async {
    loading = true;
    notifyListeners();
    final api = ApiClient();
    final response = await api.post('/auth/login', {'email': email, 'password': password});
    loading = false;
    if (response['error'] != null) {
      notifyListeners();
      return response['error'];
    }
    final data = response['data'];
    token = data['access_token'];
    role = data['role'];
    await _storage.saveCredentials(token!, role!);
    notifyListeners();
    return null;
  }

  Future<void> logout() async {
    token = null;
    role = null;
    await _storage.clear();
    notifyListeners();
  }
}
