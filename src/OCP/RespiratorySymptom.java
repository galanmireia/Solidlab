package OCP;

public class RespiratorySymptom extends Symptom {

	public RespiratorySymptom(int covidImpact, int severityIndex, String name, Double affectedDays) {
		super(severityIndex, severityIndex, name, affectedDays);

	}

	@Override
	public double getProbabilityCovid19() {
		// TODO Auto-generated method stub
		return 10;
	}

	@Override
	protected double getSeverityIndex() {
		// TODO Auto-generated method stub
		return 2;
	}

	public double geaffectedDays() {
		return 6;
	}
}
