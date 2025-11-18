import 'dart:convert';
import 'package:http/http.dart' as http;

const String apiBaseUrl = "https://YOUR-RAILWAY-API-URL";

class ApiClient {
  final String? token;
  ApiClient({this.token});

  Map<String, String> _headers() {
    final headers = {'Content-Type': 'application/json'};
    if (token != null) {
      headers['Authorization'] = 'Bearer $token';
    }
    return headers;
  }

  Future<Map<String, dynamic>> post(String path, Map<String, dynamic> body) async {
    final response = await http.post(
      Uri.parse('$apiBaseUrl$path'),
      headers: _headers(),
      body: jsonEncode(body),
    );
    return _decode(response);
  }

  Future<Map<String, dynamic>> get(String path) async {
    final response = await http.get(
      Uri.parse('$apiBaseUrl$path'),
      headers: _headers(),
    );
    return _decode(response);
  }

  Map<String, dynamic> _decode(http.Response response) {
    final data = jsonDecode(response.body);
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return {'data': data};
    }
    return {'error': data['message'] ?? 'Request failed'};
  }
}
