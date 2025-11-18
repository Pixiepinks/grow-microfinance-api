import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/api_client.dart';
import '../../core/auth_storage.dart';
import '../../widgets/summary_card.dart';
import 'loan_details_screen.dart';
import '../auth/auth_provider.dart';

class CustomerDashboard extends StatefulWidget {
  const CustomerDashboard({super.key});

  @override
  State<CustomerDashboard> createState() => _CustomerDashboardState();
}

class _CustomerDashboardState extends State<CustomerDashboard> {
  Map<String, dynamic>? _profile;
  Map<String, dynamic>? _summary;
  List<dynamic> _loans = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadData();
  }

  Future<void> _loadData() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    final auth = Provider.of<AuthProvider>(context, listen: false);
    final client = ApiClient(token: auth.token);
    final profileResponse = await client.get('/customer/me');
    final loansResponse = await client.get('/customer/loans');

    if (profileResponse['error'] != null) {
      setState(() {
        _error = profileResponse['error'];
        _loading = false;
      });
      return;
    }
    if (loansResponse['error'] != null) {
      setState(() {
        _error = loansResponse['error'];
        _loading = false;
      });
      return;
    }

    setState(() {
      _profile = profileResponse['data'];
      _summary = loansResponse['data']['summary'];
      _loans = loansResponse['data']['loans'];
      _loading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Customer Dashboard'),
        actions: [
          IconButton(
            icon: const Icon(Icons.logout),
            onPressed: () async {
              await Provider.of<AuthProvider>(context, listen: false).logout();
              if (!mounted) return;
              Navigator.of(context).pushReplacementNamed('/');
            },
          )
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : RefreshIndicator(
                  onRefresh: _loadData,
                  child: ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      Text(
                        'Hello, ${_profile?['full_name'] ?? ''}',
                        style: Theme.of(context).textTheme.headlineSmall,
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: SummaryCard(
                              title: 'Active Loans',
                              value: (_summary?['total_active_loans'] ?? 0).toString(),
                            ),
                          ),
                          Expanded(
                            child: SummaryCard(
                              title: 'Outstanding',
                              value: 'LKR ${(_summary?['total_outstanding'] ?? 0).toString()}',
                            ),
                          ),
                          Expanded(
                            child: SummaryCard(
                              title: 'Arrears',
                              value: 'LKR ${(_summary?['total_arrears'] ?? 0).toString()}',
                              valueColor: Colors.red,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 16),
                      ..._loans.map((loan) => Card(
                            child: ListTile(
                              title: Text('Loan #${loan['loan_number']}'),
                              subtitle: Text('Outstanding: LKR ${loan['outstanding']}'),
                              trailing: Column(
                                crossAxisAlignment: CrossAxisAlignment.end,
                                mainAxisAlignment: MainAxisAlignment.center,
                                children: [
                                  Text('Paid: ${loan['total_paid']}'),
                                  Text(
                                    'Arrears: ${loan['arrears']}',
                                    style: const TextStyle(color: Colors.red),
                                  ),
                                ],
                              ),
                              onTap: () {
                                Navigator.of(context).push(
                                  MaterialPageRoute(
                                    builder: (_) => LoanDetailsScreen(loanId: loan['id'], loanNumber: loan['loan_number']),
                                  ),
                                );
                              },
                            ),
                          )),
                    ],
                  ),
                ),
    );
  }
}
