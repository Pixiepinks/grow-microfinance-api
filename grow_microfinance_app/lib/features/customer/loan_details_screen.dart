import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/api_client.dart';
import '../auth/auth_provider.dart';

class LoanDetailsScreen extends StatefulWidget {
  final int loanId;
  final String loanNumber;
  const LoanDetailsScreen({super.key, required this.loanId, required this.loanNumber});

  @override
  State<LoanDetailsScreen> createState() => _LoanDetailsScreenState();
}

class _LoanDetailsScreenState extends State<LoanDetailsScreen> {
  Map<String, dynamic>? _loan;
  List<dynamic> _payments = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final auth = Provider.of<AuthProvider>(context, listen: false);
    final client = ApiClient(token: auth.token);
    final response = await client.get('/customer/loans/${widget.loanId}/payments');
    if (response['error'] != null) {
      setState(() {
        _error = response['error'];
        _loading = false;
      });
      return;
    }
    setState(() {
      _loan = response['data']['loan'];
      _payments = response['data']['payments'];
      _loading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('Loan ${widget.loanNumber}')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : Padding(
                  padding: const EdgeInsets.all(16.0),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Principal: LKR ${_loan?['principal_amount']}'),
                      Text('Total Payable: LKR ${_loan?['total_payable']}'),
                      Text('Paid: LKR ${_loan?['total_paid']}'),
                      Text('Outstanding: LKR ${_loan?['outstanding']}'),
                      Text(
                        'Arrears: LKR ${_loan?['arrears']}',
                        style: const TextStyle(color: Colors.red),
                      ),
                      const SizedBox(height: 16),
                      const Text('Payments', style: TextStyle(fontWeight: FontWeight.bold)),
                      const SizedBox(height: 8),
                      Expanded(
                        child: ListView.separated(
                          itemCount: _payments.length,
                          separatorBuilder: (_, __) => const Divider(),
                          itemBuilder: (context, index) {
                            final payment = _payments[index];
                            return ListTile(
                              title: Text('LKR ${payment['amount_collected']}'),
                              subtitle: Text('${payment['collection_date']} · ${payment['payment_method']}'),
                              trailing: Text(payment['remarks'] ?? ''),
                            );
                          },
                        ),
                      )
                    ],
                  ),
                ),
    );
  }
}
